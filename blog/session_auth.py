from datetime import datetime, timedelta, timezone as datetime_timezone

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed, InvalidToken
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)

from .models import BlogLoginSession
from .network import get_client_ip
from .network_operator import lookup_network_operator


SESSION_ID_CLAIM = 'session_id'


def _token_expiry(token):
    return datetime.fromtimestamp(
        int(token['exp']),
        tz=datetime_timezone.utc,
    )


def build_device_label(user_agent):
    """Create a short device description without retaining the full user agent."""
    value = str(user_agent or '')

    if 'Edg/' in value:
        browser = 'Edge'
    elif 'OPR/' in value or 'Opera' in value:
        browser = 'Opera'
    elif 'Firefox/' in value:
        browser = 'Firefox'
    elif 'Chrome/' in value or 'CriOS/' in value:
        browser = 'Chrome'
    elif 'Safari/' in value:
        browser = 'Safari'
    else:
        browser = 'Неизвестен браузър'

    if 'Android' in value:
        operating_system = 'Android'
    elif 'iPhone' in value or 'iPad' in value or 'iPod' in value:
        operating_system = 'iOS'
    elif 'Windows' in value:
        operating_system = 'Windows'
    elif 'Macintosh' in value or 'Mac OS X' in value:
        operating_system = 'macOS'
    elif 'Linux' in value:
        operating_system = 'Linux'
    else:
        operating_system = 'неизвестно устройство'

    return f'{browser} · {operating_system}'[:120]


def issue_session_tokens(user, request, auth_method):
    """Create a persisted login session and its refresh/access token pair."""
    now = timezone.now()
    ip_address = get_client_ip(request)
    network_operator, network_asn = lookup_network_operator(ip_address)
    fallback_expiry = now + settings.SIMPLE_JWT['REFRESH_TOKEN_LIFETIME']

    with transaction.atomic():
        login_session = BlogLoginSession.objects.create(
            user=user,
            auth_method=auth_method,
            ip_address=ip_address,
            network_operator=network_operator,
            network_asn=network_asn,
            device_label=build_device_label(
                request.META.get('HTTP_USER_AGENT', '')
            ),
            last_seen_at=now,
            expires_at=fallback_expiry,
        )
        refresh = RefreshToken.for_user(user)
        refresh[SESSION_ID_CLAIM] = str(login_session.pk)
        login_session.refresh_jti = str(refresh['jti'])
        login_session.expires_at = _token_expiry(refresh)
        login_session.save(update_fields=('refresh_jti', 'expires_at'))

    return login_session, refresh


def get_current_session_id(request):
    token = getattr(request, 'auth', None)
    if not token:
        return None
    return token.get(SESSION_ID_CLAIM)


def blacklist_session_refresh(login_session):
    if not login_session.refresh_jti:
        return
    outstanding = OutstandingToken.objects.filter(
        jti=login_session.refresh_jti,
    ).first()
    if outstanding:
        BlacklistedToken.objects.get_or_create(token=outstanding)


def revoke_login_session(login_session, reason):
    """Revoke a locked or standalone session and blacklist its refresh token."""
    if login_session.revoked_at is not None:
        return False
    login_session.revoked_at = timezone.now()
    login_session.revoked_reason = reason
    login_session.save(update_fields=('revoked_at', 'revoked_reason'))
    blacklist_session_refresh(login_session)
    return True


class SessionJWTAuthentication(JWTAuthentication):
    """Reject access tokens whose server-side session is no longer active."""

    def authenticate(self, request):
        authenticated = super().authenticate(request)
        if authenticated is None:
            return None

        user, validated_token = authenticated
        session_id = validated_token.get(SESSION_ID_CLAIM)
        if not session_id:
            raise AuthenticationFailed(
                'Сесията не е валидна. Моля, влезте отново.',
                code='session_not_found',
            )

        try:
            login_session = BlogLoginSession.objects.get(
                pk=session_id,
                user=user,
            )
        except (BlogLoginSession.DoesNotExist, ValueError, TypeError):
            raise AuthenticationFailed(
                'Сесията не е валидна. Моля, влезте отново.',
                code='session_not_found',
            )

        now = timezone.now()
        if login_session.revoked_at is not None:
            raise AuthenticationFailed(
                'Сесията е прекратена. Моля, влезте отново.',
                code='session_revoked',
            )
        if login_session.expires_at <= now:
            raise AuthenticationFailed(
                'Сесията е изтекла. Моля, влезте отново.',
                code='session_expired',
            )

        update_seconds = int(
            getattr(settings, 'BLOG_SESSION_LAST_SEEN_UPDATE_SECONDS', 900)
        )
        stale_before = now - timedelta(seconds=max(update_seconds, 60))
        if login_session.last_seen_at <= stale_before:
            BlogLoginSession.objects.filter(
                pk=login_session.pk,
                last_seen_at__lte=stale_before,
                revoked_at__isnull=True,
            ).update(last_seen_at=now)
            login_session.last_seen_at = now

        request.blog_login_session = login_session
        return user, validated_token


class SessionTokenRefreshSerializer(TokenRefreshSerializer):
    """Rotate tokens only while their persisted login session is active."""

    def validate(self, attrs):
        raw_refresh = attrs.get('refresh')
        try:
            refresh = self.token_class(raw_refresh)
            session_id = refresh.get(SESSION_ID_CLAIM)
            user_id_claim = settings.SIMPLE_JWT.get('USER_ID_CLAIM', 'user_id')
            user_id = refresh.get(user_id_claim)
            if not session_id or user_id is None:
                raise InvalidToken(
                    'Сесията не е валидна. Моля, влезте отново.'
                )

            with transaction.atomic():
                try:
                    login_session = BlogLoginSession.objects.select_for_update().get(
                        pk=session_id,
                        user_id=user_id,
                    )
                except (BlogLoginSession.DoesNotExist, ValueError, TypeError):
                    raise InvalidToken(
                        'Сесията не е валидна. Моля, влезте отново.'
                    )

                now = timezone.now()
                if (
                    login_session.revoked_at is not None
                    or login_session.expires_at <= now
                    or login_session.refresh_jti != str(refresh['jti'])
                ):
                    raise InvalidToken(
                        'Сесията е прекратена или изтекла. Моля, влезте отново.'
                    )

                data = super().validate(attrs)
                rotated_refresh = data.get('refresh')
                if rotated_refresh:
                    new_refresh = RefreshToken(rotated_refresh)
                    login_session.refresh_jti = str(new_refresh['jti'])
                    login_session.expires_at = _token_expiry(new_refresh)
                    login_session.save(
                        update_fields=('refresh_jti', 'expires_at')
                    )
                return data
        except InvalidToken:
            raise
        except Exception as exc:
            from rest_framework_simplejwt.exceptions import TokenError

            if isinstance(exc, TokenError):
                raise InvalidToken(exc.args[0]) from exc
            raise
