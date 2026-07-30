import logging
import random
import re
import string

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from .models import GoogleIdentity, SiteSettings, UserProfile
from .permissions import SiteIsAvailable


logger = logging.getLogger(__name__)
User = get_user_model()
GOOGLE_ISSUER = 'https://accounts.google.com'
VALID_GOOGLE_ISSUERS = {'accounts.google.com', GOOGLE_ISSUER}


def _verified_email(id_info):
    email = id_info.get('email')
    verified = id_info.get('email_verified')
    if isinstance(verified, str):
        verified = verified.lower() == 'true'
    if not email or verified is not True:
        return None
    return User.objects.normalize_email(email).strip().lower()


def _generate_available_username(email):
    base_name = re.sub(r'[^\w.@+-]', '', email.split('@', 1)[0]) or 'user'
    base_name = base_name[:140]
    alphabet = string.ascii_lowercase + string.digits
    for _ in range(100):
        suffix = ''.join(random.SystemRandom().choices(alphabet, k=8))
        username = f"{base_name}-{suffix}"[:150]
        if not User.objects.filter(username__iexact=username).exists():
            return username
    raise RuntimeError("Unable to allocate a unique username")


class GoogleAuthView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = 'google_auth'

    def post(self, request):
        token = request.data.get('credential')
        if not token:
            return Response(
                {'error': 'Липсва Google credential token.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not getattr(settings, 'GOOGLE_CLIENT_ID', '').strip():
            return Response(
                {'error': 'Входът с Google временно не е достъпен.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        try:
            id_info = id_token.verify_oauth2_token(
                token,
                google_requests.Request(),
                settings.GOOGLE_CLIENT_ID,
            )
        except ValueError:
            return Response(
                {'error': 'Невалиден Google token.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception:
            logger.exception("Google token verification failed unexpectedly")
            return Response(
                {'error': 'Входът с Google временно не е достъпен.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        issuer = id_info.get('iss')
        subject = id_info.get('sub')
        email = _verified_email(id_info)
        if issuer not in VALID_GOOGLE_ISSUERS or not subject or not email:
            return Response(
                {'error': 'Невалиден Google token.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            with transaction.atomic():
                site_settings, _ = (
                    SiteSettings.objects.select_for_update()
                    .get_or_create(pk=1)
                )
                identity = (
                    GoogleIdentity.objects.select_for_update()
                    .select_related('user')
                    .filter(issuer=GOOGLE_ISSUER, subject=subject)
                    .first()
                )

                if identity:
                    user = identity.user
                    if not user.is_active:
                        return Response(
                            {'error': 'Акаунтът не е достъпен.'},
                            status=status.HTTP_403_FORBIDDEN,
                        )
                    if identity.email_at_link_time != email:
                        identity.email_at_link_time = email
                        identity.save(update_fields=['email_at_link_time', 'updated_at'])
                else:
                    # E-mail alone is not proof of ownership for linking. An
                    # existing local account must be linked from an authenticated
                    # profile flow rather than silently claimed here. The sole
                    # exception is an unambiguous legacy Google-only account:
                    # the old implementation left its password as exactly ''.
                    email_matches = list(
                        User.objects.select_for_update()
                        .filter(email__iexact=email)
                        .order_by('pk')[:2]
                    )
                    if (
                        len(email_matches) == 1
                        and email_matches[0].is_active
                        and email_matches[0].password == ''
                    ):
                        user = email_matches[0]
                        user.email = email
                        user.set_unusable_password()
                        user.save(update_fields=['email', 'password'])
                        UserProfile.objects.get_or_create(user=user)
                        GoogleIdentity.objects.create(
                            user=user,
                            issuer=GOOGLE_ISSUER,
                            subject=subject,
                            email_at_link_time=email,
                        )
                    elif email_matches:
                        return Response(
                            {
                                'error': (
                                    'Вече съществува акаунт с този имейл. '
                                    'Влезте по обичайния начин, за да го свържете.'
                                )
                            },
                            status=status.HTTP_409_CONFLICT,
                        )
                    else:
                        if site_settings.maintenance_mode:
                            return Response(
                                {
                                    'error': (
                                        'Сайтът временно е в режим '
                                        'на поддръжка.'
                                    )
                                },
                                status=status.HTTP_503_SERVICE_UNAVAILABLE,
                            )
                        if not site_settings.enable_user_registration:
                            return Response(
                                {
                                    'error': (
                                        'Регистрацията временно е деактивирана.'
                                    )
                                },
                                status=status.HTTP_403_FORBIDDEN,
                            )

                        user = User(
                            username=_generate_available_username(email),
                            email=email,
                            first_name=(
                                id_info.get('given_name') or ''
                            ).strip(),
                            last_name=(
                                id_info.get('family_name') or ''
                            ).strip(),
                            is_active=True,
                        )
                        user.set_unusable_password()
                        user.save()
                        UserProfile.objects.get_or_create(user=user)
                        GoogleIdentity.objects.create(
                            user=user,
                            issuer=GOOGLE_ISSUER,
                            subject=subject,
                            email_at_link_time=email,
                        )
        except IntegrityError:
            # A concurrent request may have created the same identity. Do not
            # expose database details or guess-link by e-mail.
            identity = (
                GoogleIdentity.objects.select_related('user')
                .filter(issuer=GOOGLE_ISSUER, subject=subject)
                .first()
            )
            if not identity or not identity.user.is_active:
                return Response(
                    {'error': 'Неуспешно създаване на акаунт.'},
                    status=status.HTTP_409_CONFLICT,
                )
            user = identity.user
        except Exception:
            logger.exception("Google account sign-in failed unexpectedly")
            return Response(
                {'error': 'Входът с Google временно не е достъпен.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        refresh = RefreshToken.for_user(user)
        return Response(
            {
                'access': str(refresh.access_token),
                'refresh': str(refresh),
                'user': {
                    'id': user.id,
                    'username': user.username,
                    'email': user.email,
                },
            },
            status=status.HTTP_200_OK,
        )


class GoogleLinkView(APIView):
    """Explicitly link Google from an already authenticated local account."""

    permission_classes = [SiteIsAvailable, IsAuthenticated]
    throttle_scope = 'google_auth'

    def post(self, request):
        token = request.data.get('credential')
        if not token:
            return Response(
                {'error': 'Липсва Google credential token.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not getattr(settings, 'GOOGLE_CLIENT_ID', '').strip():
            return Response(
                {'error': 'Свързването с Google временно не е достъпно.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        try:
            id_info = id_token.verify_oauth2_token(
                token,
                google_requests.Request(),
                settings.GOOGLE_CLIENT_ID,
            )
        except ValueError:
            return Response(
                {'error': 'Невалиден Google token.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception:
            logger.exception("Google link token verification failed unexpectedly")
            return Response(
                {'error': 'Свързването с Google временно не е достъпно.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        issuer = id_info.get('iss')
        subject = id_info.get('sub')
        email = _verified_email(id_info)
        if (
            issuer not in VALID_GOOGLE_ISSUERS
            or not subject
            or not email
        ):
            return Response(
                {'error': 'Google акаунтът не съвпада с профила.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            with transaction.atomic():
                user = User.objects.select_for_update().get(pk=request.user.pk)
                local_email = User.objects.normalize_email(
                    user.email
                ).strip().lower()
                if email != local_email:
                    return Response(
                        {'error': 'Google акаунтът не съвпада с профила.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                if user.has_usable_password() and not user.check_password(
                    request.data.get('current_password', '')
                ):
                    return Response(
                        {'error': 'Невалидни данни за потвърждение.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                subject_identity = (
                    GoogleIdentity.objects.select_for_update()
                    .filter(issuer=GOOGLE_ISSUER, subject=subject)
                    .first()
                )
                if subject_identity and subject_identity.user_id != user.pk:
                    return Response(
                        {'error': 'Google акаунтът вече е свързан.'},
                        status=status.HTTP_409_CONFLICT,
                    )

                user_identity = (
                    GoogleIdentity.objects.select_for_update()
                    .filter(user=user)
                    .first()
                )
                if user_identity and (
                    user_identity.issuer != GOOGLE_ISSUER
                    or user_identity.subject != subject
                ):
                    return Response(
                        {'error': 'Профилът вече има друг Google акаунт.'},
                        status=status.HTTP_409_CONFLICT,
                    )

                identity = subject_identity or user_identity
                if identity:
                    if identity.email_at_link_time != email:
                        identity.email_at_link_time = email
                        identity.save(
                            update_fields=['email_at_link_time', 'updated_at']
                        )
                else:
                    GoogleIdentity.objects.create(
                        user=user,
                        issuer=GOOGLE_ISSUER,
                        subject=subject,
                        email_at_link_time=email,
                    )
        except IntegrityError:
            return Response(
                {'error': 'Google акаунтът не може да бъде свързан.'},
                status=status.HTTP_409_CONFLICT,
            )
        except Exception:
            logger.exception("Google account linking failed unexpectedly")
            return Response(
                {'error': 'Свързването с Google временно не е достъпно.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response(
            {'detail': 'Google акаунтът е свързан успешно.'},
            status=status.HTTP_200_OK,
        )
