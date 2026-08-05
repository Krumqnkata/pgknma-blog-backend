import mimetypes
import json

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.contrib.auth import password_validation
from django.db import IntegrityError, transaction
from django.http import FileResponse, Http404, HttpResponse, HttpResponseRedirect
from django.utils.cache import patch_vary_headers
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    OpenApiTypes,
    extend_schema,
)
from rest_framework import viewsets, generics, status
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.exceptions import (
    PermissionDenied,
    ValidationError as RestValidationError,
)
from django.shortcuts import get_object_or_404
from django.utils import timezone
from datetime import timedelta
import re
from django.db.models import BooleanField, Count, Exists, OuterRef, Q, Value
from django.db.models.functions import Coalesce
from django.contrib.auth.models import User
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from .models import Posts, Comments, PollQuestion, PollAnswer, PollOption, ContactSubmission, Notification, Event, TermsOfService, BellSongSuggestion, PrivacyPolicy, MemeOfWeek, ConsentRecord, SiteSettings, Changelog, ScheduleEntry, BlogLoginSession, UserProfile
from .permissions import IsOwner, SiteIsAvailable
from .network import get_client_ip
from .network_operator import lookup_network_operator
from .serializer import (
    PostSerializer, RegisterSerializer, CommentSerializer,
    PollQuestionSerializer, UserPollStatusSerializer, PollAnswerSerializer,
    PollStatisticsSerializer, ContactSubmissionSerializer, NotificationSerializer,
    EventSerializer, TermsOfServiceSerializer, BellSongSuggestionSerializer,
    PrivacyPolicySerializer, MemeOfWeekSerializer, ConsentRecordSerializer, SiteSettingsSerializer,
    ChangelogSerializer, PasswordChangeSerializer, UsernameChangeSerializer, ScheduleEntrySerializer, BlogLoginSessionSerializer,
    ApiDetailResponseSerializer, BlogSessionRevokeOthersResponseSerializer, PersonalDataExportSchemaSerializer,
    CurrentUserSerializer, ProfileAvatarUpdateSerializer,
    CurrentProfileResponseSerializer, AvatarUpdateResponseSerializer,
    RegistrationResponseSerializer, EmailVerificationResendRequestSerializer,
)
from .avatars import (
    avatar_options_payload,
    reset_profile_avatar,
    set_profile_avatar,
    set_profile_preset,
)
from .personal_data_export import build_personal_data_export
from .email_verification import (
    resend_is_on_cooldown,
    send_verification_email,
    verify_email_token,
)
from .session_auth import (
    SESSION_ID_CLAIM,
    SessionJWTAuthentication,
    get_current_session_id,
    revoke_login_session,
)


def _with_vote_status(queryset, request, relation):
    if not request.user.is_authenticated:
        return queryset.annotate(
            user_has_voted=Value(False, output_field=BooleanField())
        )
    through = relation.through
    related_field = (
        'memeofweek_id'
        if relation is MemeOfWeek.voted_by
        else 'bellsongsuggestion_id'
    )
    return queryset.annotate(
        user_has_voted=Exists(
            through.objects.filter(
                **{
                    related_field: OuterRef('pk'),
                    'user_id': request.user.pk,
                }
            )
        )
    )


class PostViewSet(viewsets.ModelViewSet):
    serializer_class = PostSerializer
    permission_classes = [SiteIsAvailable, AllowAny]
    http_method_names = ['get', 'head', 'options']

    def get_queryset(self):
        now = timezone.now()
        return (
            Posts.objects.filter(published=True, allowed=True)
            .filter(Q(published_at__isnull=True) | Q(published_at__lte=now))
            .select_related('author', 'author__userprofile', 'category')
            .prefetch_related('images', 'documents')
            .annotate(visible_at=Coalesce('published_at', 'created_at'))
            .order_by('-visible_at')
        )


class ProtectedMemeImageView(APIView):
    """Serve meme files without exposing pending uploads through MEDIA_URL."""

    authentication_classes = [SessionJWTAuthentication, SessionAuthentication]
    permission_classes = [SiteIsAvailable, AllowAny]
    http_method_names = ['get', 'head', 'options']

    def get(self, request, pk):
        meme = get_object_or_404(
            MemeOfWeek.objects.only(
                'pk',
                'user_id',
                'image',
                'status',
            ),
            pk=pk,
        )
        user = request.user
        is_authenticated = bool(user and user.is_authenticated)
        is_owner = is_authenticated and meme.user_id == user.pk
        is_staff = is_authenticated and user.is_staff

        if not (is_owner or is_staff):
            feature_enabled = (
                SiteSettings.objects.filter(pk=1)
                .values_list('enable_meme_of_the_week', flat=True)
                .first()
            )
            if meme.status != 'approved' or feature_enabled is False:
                raise Http404

        if not meme.image or not meme.image.name:
            raise Http404

        try:
            image_file = meme.image.open('rb')
        except (FileNotFoundError, OSError, ValueError):
            raise Http404

        content_type, _ = mimetypes.guess_type(meme.image.name)
        unsafe_inline_type = (
            not content_type
            or not content_type.startswith('image/')
            or content_type == 'image/svg+xml'
        )
        if unsafe_inline_type:
            content_type = 'application/octet-stream'

        response = FileResponse(
            image_file,
            as_attachment=unsafe_inline_type,
            filename=meme.image.name.rsplit('/', 1)[-1],
            content_type=content_type,
        )
        response['Cache-Control'] = 'private, no-store'
        response['Pragma'] = 'no-cache'
        response['X-Content-Type-Options'] = 'nosniff'
        patch_vary_headers(response, ('Authorization', 'Cookie'))
        return response


def blocked_meme_media(request, media_path=None):
    """Never serve meme uploads directly from the public MEDIA_URL."""

    raise Http404


class MemeOfWeekViewSet(viewsets.ModelViewSet):
    serializer_class = MemeOfWeekSerializer
    http_method_names = ['get', 'post', 'head', 'options']

    def get_queryset(self):
        site_settings = SiteSettings.objects.filter(pk=1).first()
        if site_settings and not site_settings.enable_meme_of_the_week:
            return MemeOfWeek.objects.none()
        queryset = (
            MemeOfWeek.objects.filter(status='approved')
            .select_related('user')
            .order_by('-votes', '-created_at')
        )
        return _with_vote_status(queryset, self.request, MemeOfWeek.voted_by)

    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            self.permission_classes = [SiteIsAvailable, AllowAny]
        else:
            self.permission_classes = [SiteIsAvailable, IsAuthenticated]
        return super().get_permissions()

    def get_throttles(self):
        self.throttle_scope = (
            'meme_submit' if self.action == 'create' else None
        )
        return super().get_throttles()

    def perform_create(self, serializer):
        site_settings, created = SiteSettings.objects.get_or_create(pk=1)
        if not site_settings.enable_meme_of_the_week:
            raise PermissionDenied("Функцията 'Меме на седмицата' в момента е деактивирана.")
        serializer.save(user=self.request.user)
class RegisterView(generics.CreateAPIView):
    permission_classes = [SiteIsAvailable, AllowAny]
    throttle_scope = 'register'
    queryset = User.objects.all()
    serializer_class = RegisterSerializer

    def post(self, request, *args, **kwargs):
        site_settings, created = SiteSettings.objects.get_or_create(pk=1)
        if not site_settings.enable_user_registration:
            return Response(
                {"detail": "Регистрацията на потребители е временно деактивирана."},
                status=status.HTTP_403_FORBIDDEN
            )
        return super().post(request, *args, **kwargs)

    @extend_schema(
        operation_id='auth_register',
        summary='Регистрация с имейл, парола и последващо потвърждение',
        request=RegisterSerializer,
        responses={201: RegistrationResponseSerializer},
        tags=['Автентикация'],
    )
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        delivery = send_verification_email(user)
        profile = UserProfile.objects.get(user=user)

        if delivery.auto_verified:
            detail = (
                'Профилът е създаден и потвърден автоматично, защото '
                'имейл услугата временно не е достъпна.'
            )
        elif delivery.sent:
            detail = (
                'Изпратихме линк за потвърждение. Проверете и папка „Спам“. '
                'Точният срок на валидност е посочен в писмото.'
            )
        else:
            detail = (
                'Профилът е създаден, но писмото не можа да бъде изпратено. '
                'Използвайте „Изпрати отново“ след малко.'
            )

        headers = self.get_success_headers(serializer.data)
        return Response(
            {
                'username': user.username,
                'email': user.email,
                'verification_required': not profile.email_verified,
                'email_sent': delivery.sent,
                'auto_verified': delivery.auto_verified,
                'detail': detail,
            },
            status=status.HTTP_201_CREATED,
            headers=headers,
        )


class EmailVerificationConfirmView(APIView):
    permission_classes = (AllowAny,)
    authentication_classes = ()

    @extend_schema(exclude=True)
    def get(self, request):
        result = verify_email_token(request.query_params.get('token', ''))
        target = f'{settings.FRONTEND_URL}/verify-email?status={result}'
        return HttpResponseRedirect(target)


class EmailVerificationResendView(APIView):
    permission_classes = (AllowAny,)
    authentication_classes = ()
    throttle_scope = 'email_verification_resend'

    @extend_schema(
        operation_id='email_verification_resend',
        summary='Повторно изпращане на имейл за потвърждение',
        request=EmailVerificationResendRequestSerializer,
        responses={202: ApiDetailResponseSerializer},
        tags=['Автентикация'],
    )
    def post(self, request):
        payload = EmailVerificationResendRequestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        email = User.objects.normalize_email(
            payload.validated_data['email']
        ).strip().lower()
        if email:
            user = User.objects.filter(email__iexact=email).first()
            if user:
                profile, _ = UserProfile.objects.get_or_create(user=user)
                if (
                    user.is_active
                    and not profile.email_verified
                    and not resend_is_on_cooldown(user)
                ):
                    send_verification_email(user)

        return Response(
            {
                'detail': (
                    'Ако има непотвърден профил с този имейл, изпратихме '
                    'нов линк или приложихме аварийната настройка.'
                )
            },
            status=status.HTTP_202_ACCEPTED,
        )


class CurrentProfileView(APIView):
    permission_classes = (SiteIsAvailable, IsAuthenticated)

    @extend_schema(
        operation_id='profile_current',
        summary='Текущ профил и настройки за аватара',
        request=None,
        tags=['Профил'],
        responses={200: CurrentProfileResponseSerializer},
    )
    def get(self, request):
        UserProfile.objects.get_or_create(user=request.user)
        user = User.objects.select_related('userprofile').get(pk=request.user.pk)
        return Response(
            {
                'user': CurrentUserSerializer(
                    user,
                    context={'request': request},
                ).data,
                'avatar_options': avatar_options_payload(),
            },
            status=status.HTTP_200_OK,
        )


class ProfileAvatarView(APIView):
    permission_classes = (SiteIsAvailable, IsAuthenticated)
    parser_classes = (MultiPartParser, FormParser, JSONParser)
    throttle_scope = 'profile_avatar'

    def _profile(self, request):
        UserProfile.objects.get_or_create(user=request.user)
        return UserProfile.objects.select_for_update().get(user=request.user)

    def _response(self, request):
        user = User.objects.select_related('userprofile').get(pk=request.user.pk)
        return Response(
            {
                'user': CurrentUserSerializer(
                    user,
                    context={'request': request},
                ).data,
            },
            status=status.HTTP_200_OK,
        )

    @staticmethod
    def _raise_validation_error(exc):
        detail = getattr(exc, 'message_dict', None) or {
            'avatar': getattr(exc, 'messages', [str(exc)])
        }
        raise RestValidationError(detail)

    @extend_schema(
        operation_id='profile_avatar_update',
        summary='Качване на снимка или избор на готов аватар',
        request=ProfileAvatarUpdateSerializer,
        tags=['Профил'],
        responses={200: AvatarUpdateResponseSerializer},
    )
    def put(self, request):
        serializer = ProfileAvatarUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                profile = self._profile(request)
                if 'avatar' in serializer.validated_data:
                    set_profile_avatar(
                        profile,
                        serializer.validated_data['avatar'],
                        source=UserProfile.AvatarSource.UPLOAD,
                        user_selected=True,
                    )
                else:
                    set_profile_preset(
                        profile,
                        serializer.validated_data['preset_id'],
                    )
        except ValidationError as exc:
            self._raise_validation_error(exc)
        return self._response(request)

    @extend_schema(
        operation_id='profile_avatar_reset',
        summary='Връщане към инициали',
        request=None,
        tags=['Профил'],
        responses={200: AvatarUpdateResponseSerializer},
    )
    def delete(self, request):
        with transaction.atomic():
            profile = self._profile(request)
            reset_profile_avatar(profile, user_selected=True)
        return self._response(request)


class PasswordChangeView(generics.UpdateAPIView):
    serializer_class = PasswordChangeSerializer
    model = User
    permission_classes = (SiteIsAvailable, IsAuthenticated)

    def get_object(self, queryset=None):
        return self.request.user

    def update(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)

        if serializer.is_valid():
            with transaction.atomic():
                self.object = User.objects.select_for_update().get(
                    pk=request.user.pk
                )
                if not self.object.check_password(
                    serializer.validated_data["current_password"]
                ):
                    return Response(
                        {"current_password": ["Грешна парола."]},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                self.object.set_password(
                    serializer.validated_data["new_password"]
                )
                self.object.save(update_fields=['password'])
                active_sessions = BlogLoginSession.objects.select_for_update().filter(
                    user=self.object,
                    revoked_at__isnull=True,
                )
                for login_session in active_sessions:
                    revoke_login_session(
                        login_session,
                        BlogLoginSession.REVOKED_PASSWORD_CHANGE,
                    )
            return Response({"status": "Паролата е сменена успешно"}, status=status.HTTP_200_OK)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class UsernameChangeView(generics.UpdateAPIView):
    serializer_class = UsernameChangeSerializer
    model = User
    permission_classes = (SiteIsAvailable, IsAuthenticated)

    def get_object(self, queryset=None):
        return self.request.user

    def update(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)

        if serializer.is_valid():
            try:
                with transaction.atomic():
                    SiteSettings.objects.select_for_update().get_or_create(pk=1)
                    self.object = User.objects.select_for_update().get(
                        pk=request.user.pk
                    )
                    if not self.object.check_password(
                        serializer.validated_data["current_password"]
                    ):
                        return Response(
                            {"current_password": ["Грешна парола."]},
                            status=status.HTTP_400_BAD_REQUEST,
                        )
                    new_username = serializer.validated_data["new_username"]
                    if User.objects.exclude(pk=self.object.pk).filter(
                        username__iexact=new_username
                    ).exists():
                        return Response(
                            {
                                "new_username": [
                                    "Това потребителско име вече е заето."
                                ]
                            },
                            status=status.HTTP_400_BAD_REQUEST,
                        )
                    self.object.username = new_username
                    self.object.save(update_fields=['username'])
            except IntegrityError:
                return Response(
                    {
                        "new_username": [
                            "Това потребителско име вече е заето."
                        ]
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            return Response({"status": "Потребителското име е сменено успешно"}, status=status.HTTP_200_OK)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class AccountDeactivateView(APIView):
    permission_classes = (SiteIsAvailable, IsAuthenticated)

    def _deactivate(self, request):
        with transaction.atomic():
            instance = User.objects.select_for_update().get(
                pk=request.user.pk
            )
            profile, _ = UserProfile.objects.get_or_create(user=instance)
            profile = UserProfile.objects.select_for_update().get(pk=profile.pk)
            reset_profile_avatar(profile, user_selected=True)
            instance.is_active = False
            instance.save(update_fields=['is_active'])
            active_sessions = BlogLoginSession.objects.select_for_update().filter(
                user=instance,
                revoked_at__isnull=True,
            )
            for login_session in active_sessions:
                revoke_login_session(
                    login_session,
                    BlogLoginSession.REVOKED_ACCOUNT_DEACTIVATED,
                )
        return Response({"status": "Профилът е деактивиран успешно"}, status=status.HTTP_200_OK)

    def post(self, request, *args, **kwargs):
        return self._deactivate(request)

    def put(self, request, *args, **kwargs):
        return self._deactivate(request)

    def patch(self, request, *args, **kwargs):
        return self._deactivate(request)


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        refresh_token = request.data.get('refresh')
        if not refresh_token:
            return Response(
                {"detail": "Липсва refresh token."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            token = RefreshToken(refresh_token)
            user_id_claim = settings.SIMPLE_JWT.get(
                'USER_ID_CLAIM',
                'user_id',
            )
            if str(token.get(user_id_claim)) != str(request.user.pk):
                raise TokenError()
            token_session_id = token.get(SESSION_ID_CLAIM)
            current_session_id = get_current_session_id(request)
            if token_session_id:
                if str(token_session_id) != str(current_session_id):
                    raise TokenError()
                with transaction.atomic():
                    login_session = BlogLoginSession.objects.select_for_update().get(
                        pk=token_session_id,
                        user=request.user,
                        refresh_jti=str(token['jti']),
                    )
                    revoke_login_session(
                        login_session,
                        BlogLoginSession.REVOKED_LOGOUT,
                    )
            else:
                # Backwards-compatible cleanup for a token issued before
                # server-side sessions were deployed.
                token.blacklist()
        except (TokenError, BlogLoginSession.DoesNotExist, ValueError, TypeError):
            return Response(
                {"detail": "Невалиден refresh token."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(status=status.HTTP_205_RESET_CONTENT)


class BlogSessionListView(generics.ListAPIView):
    serializer_class = BlogLoginSessionSerializer
    permission_classes = (SiteIsAvailable, IsAuthenticated)

    @extend_schema(
        operation_id='profile_session_history',
        summary='История на сесиите на текущия потребител',
        description=(
            'Връща до 100 собствени сесии в рамките на настроения срок за '
            'съхранение. Полето `status` е `active`, `expired` или `revoked`, '
            'а `is_current` показва сесията, с която е изпратена заявката. '
            'Изисква валиден JWT Bearer access token.'
        ),
        tags=['Профил – сесии'],
        responses={
            200: OpenApiResponse(
                response=BlogLoginSessionSerializer(many=True),
                description='Списък със собствените сесии, подредени от най-новата.',
                examples=[
                    OpenApiExample(
                        'Активна сесия',
                        value={
                            'id': '22c7a26f-31de-48d7-b175-cdb37e49e264',
                            'auth_method': 'google',
                            'auth_method_display': 'Google',
                            'ip_address': '203.0.113.10',
                            'network_operator': 'Example Network',
                            'network_asn': 64500,
                            'device_label': 'Firefox · Windows',
                            'created_at': '2026-08-01T10:00:00+03:00',
                            'last_seen_at': '2026-08-01T10:15:00+03:00',
                            'expires_at': '2026-08-08T10:00:00+03:00',
                            'revoked_at': None,
                            'revoked_reason': '',
                            'revoked_reason_display': '',
                            'status': 'active',
                            'is_current': True,
                        },
                        response_only=True,
                    )
                ],
            ),
            401: OpenApiResponse(
                response=ApiDetailResponseSerializer,
                description='Липсващ, изтекъл или прекратен JWT token.',
            ),
            429: OpenApiResponse(
                response=ApiDetailResponseSerializer,
                description='Надвишен лимит на заявките.',
            ),
            503: OpenApiResponse(
                response=ApiDetailResponseSerializer,
                description='Сайтът е в режим на поддръжка.',
            ),
        },
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        history_cutoff = timezone.now() - timedelta(
            days=getattr(settings, 'BLOG_SESSION_RETENTION_DAYS', 90)
        )
        return BlogLoginSession.objects.filter(
            Q(user=self.request.user),
            Q(revoked_at__gte=history_cutoff)
            | Q(revoked_at__isnull=True, expires_at__gte=history_cutoff),
        ).order_by('-created_at')[:100]

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['current_session_id'] = get_current_session_id(self.request)
        return context


class BlogSessionRevokeView(APIView):
    permission_classes = (SiteIsAvailable, IsAuthenticated)
    throttle_scope = 'session_management'

    @extend_schema(
        operation_id='profile_session_revoke',
        summary='Прекратяване на една друга сесия',
        description=(
            'Прекратява собствена сесия по UUID. Текущата сесия не може да '
            'бъде прекратена от този endpoint; за нея се използва изходът от '
            'профила. Операцията е идемпотентна за вече прекратена сесия.'
        ),
        tags=['Профил – сесии'],
        request=None,
        parameters=[
            OpenApiParameter(
                name='session_id',
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.PATH,
                description='UUID на собствената сесия за прекратяване.',
            )
        ],
        responses={
            200: OpenApiResponse(
                response=ApiDetailResponseSerializer,
                description='Сесията е прекратена или вече е била прекратена.',
                examples=[
                    OpenApiExample(
                        'Успешно прекратяване',
                        value={'detail': 'Сесията е прекратена успешно.'},
                        response_only=True,
                    )
                ],
            ),
            400: OpenApiResponse(
                response=ApiDetailResponseSerializer,
                description='Направен е опит за прекратяване на текущата сесия.',
            ),
            401: OpenApiResponse(
                response=ApiDetailResponseSerializer,
                description='Липсващ, изтекъл или прекратен JWT token.',
            ),
            404: OpenApiResponse(
                response=ApiDetailResponseSerializer,
                description='Сесията не съществува или принадлежи на друг потребител.',
            ),
            429: OpenApiResponse(
                response=ApiDetailResponseSerializer,
                description='Надвишен лимит за управление на сесии.',
            ),
            503: OpenApiResponse(
                response=ApiDetailResponseSerializer,
                description='Сайтът е в режим на поддръжка.',
            ),
        },
    )
    def post(self, request, session_id):
        current_session_id = get_current_session_id(request)
        if str(session_id) == str(current_session_id):
            return Response(
                {"detail": "Използвайте бутона „Изход“, за да прекратите текущата сесия."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            login_session = get_object_or_404(
                BlogLoginSession.objects.select_for_update(),
                pk=session_id,
                user=request.user,
            )
            changed = revoke_login_session(
                login_session,
                BlogLoginSession.REVOKED_USER,
            )
        return Response(
            {
                "detail": (
                    "Сесията е прекратена успешно."
                    if changed
                    else "Сесията вече е прекратена."
                )
            },
            status=status.HTTP_200_OK,
        )


class BlogSessionRevokeOthersView(APIView):
    permission_classes = (SiteIsAvailable, IsAuthenticated)
    throttle_scope = 'session_management'

    @extend_schema(
        operation_id='profile_sessions_revoke_others',
        summary='Прекратяване на всички други активни сесии',
        description=(
            'Прекратява всички активни сесии на текущия потребител без '
            'сесията, с която е изпратена заявката.'
        ),
        tags=['Профил – сесии'],
        request=None,
        responses={
            200: OpenApiResponse(
                response=BlogSessionRevokeOthersResponseSerializer,
                description='Операцията е изпълнена успешно.',
                examples=[
                    OpenApiExample(
                        'Прекратени две сесии',
                        value={
                            'detail': 'Всички други активни сесии са прекратени.',
                            'revoked_count': 2,
                        },
                        response_only=True,
                    )
                ],
            ),
            400: OpenApiResponse(
                response=ApiDetailResponseSerializer,
                description='Текущата server-side сесия не може да бъде разпозната.',
            ),
            401: OpenApiResponse(
                response=ApiDetailResponseSerializer,
                description='Липсващ, изтекъл или прекратен JWT token.',
            ),
            429: OpenApiResponse(
                response=ApiDetailResponseSerializer,
                description='Надвишен лимит за управление на сесии.',
            ),
            503: OpenApiResponse(
                response=ApiDetailResponseSerializer,
                description='Сайтът е в режим на поддръжка.',
            ),
        },
    )
    def post(self, request):
        current_session_id = get_current_session_id(request)
        if not current_session_id:
            return Response(
                {"detail": "Текущата сесия не може да бъде разпозната."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        revoked_count = 0
        with transaction.atomic():
            sessions = BlogLoginSession.objects.select_for_update().filter(
                user=request.user,
                revoked_at__isnull=True,
                expires_at__gt=timezone.now(),
            ).exclude(pk=current_session_id)
            for login_session in sessions:
                if revoke_login_session(
                    login_session,
                    BlogLoginSession.REVOKED_USER,
                ):
                    revoked_count += 1

        return Response(
            {
                "detail": "Всички други активни сесии са прекратени.",
                "revoked_count": revoked_count,
            },
            status=status.HTTP_200_OK,
        )


class PersonalDataExportView(APIView):
    permission_classes = (SiteIsAvailable, IsAuthenticated)
    throttle_scope = 'personal_data_export'

    @extend_schema(
        operation_id='profile_personal_data_export',
        summary='Изтегляне на личните данни на текущия потребител',
        description=(
            'Генерира UTF-8 JSON файл с профилните данни, създаденото '
            'съдържание, отговорите, съгласията и историята на входовете, '
            'които са пряко свързани с текущия Django потребител. Не включва '
            'парола, password hash, JWT/refresh token-и или чужди записи. '
            'Отговорът е attachment и е маркиран с `Cache-Control: no-store`.'
        ),
        tags=['Профил – лични данни'],
        responses={
            200: OpenApiResponse(
                response=PersonalDataExportSchemaSerializer,
                description='JSON файл с личните данни на текущия потребител.',
                examples=[
                    OpenApiExample(
                        'Съкратен пример',
                        value={
                            'format_version': '1.0',
                            'scope': 'pgknma-blog',
                            'generated_at': '2026-08-01T10:30:00+03:00',
                            'account': {
                                'id': 14,
                                'username': 'example-user',
                                'email': 'user@example.com',
                            },
                            'created_content': {'comments': []},
                            'activity': {'poll_answers': []},
                            'privacy_and_security': {'login_sessions': []},
                            'excluded_for_security': ['password and password hash'],
                            'notes': [],
                        },
                        response_only=True,
                    )
                ],
            ),
            401: OpenApiResponse(
                response=ApiDetailResponseSerializer,
                description='Липсващ, изтекъл или прекратен JWT token.',
            ),
            429: OpenApiResponse(
                response=ApiDetailResponseSerializer,
                description='Надвишен дневен лимит за експортиране.',
            ),
            503: OpenApiResponse(
                response=ApiDetailResponseSerializer,
                description='Сайтът е в режим на поддръжка.',
            ),
        },
    )
    def get(self, request):
        payload = build_personal_data_export(request.user)
        content = json.dumps(
            payload,
            cls=DjangoJSONEncoder,
            ensure_ascii=False,
            indent=2,
        ).encode('utf-8')
        filename = f'pgknma-personal-data-{timezone.localdate():%Y-%m-%d}.json'
        response = HttpResponse(
            content,
            content_type='application/json; charset=utf-8',
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        response['Cache-Control'] = 'private, no-store, max-age=0'
        response['Pragma'] = 'no-cache'
        response['X-Content-Type-Options'] = 'nosniff'
        return response


class MySongSuggestionsView(generics.ListAPIView):
    serializer_class = BellSongSuggestionSerializer
    permission_classes = (SiteIsAvailable, IsAuthenticated)

    def get_queryset(self):
        queryset = (
            BellSongSuggestion.objects.filter(user=self.request.user)
            .select_related('user')
        )
        return _with_vote_status(
            queryset, self.request, BellSongSuggestion.voted_by
        )

class MyMemesView(generics.ListAPIView):
    serializer_class = MemeOfWeekSerializer
    permission_classes = (SiteIsAvailable, IsAuthenticated)

    def get_queryset(self):
        queryset = (
            MemeOfWeek.objects.filter(user=self.request.user)
            .select_related('user')
        )
        return _with_vote_status(queryset, self.request, MemeOfWeek.voted_by)

    def get_serializer_context(self):
        return {'request': self.request}

class MyCommentsView(generics.ListAPIView):
    serializer_class = CommentSerializer
    permission_classes = (SiteIsAvailable, IsAuthenticated)

    def get_queryset(self):
        return (
            Comments.objects.filter(user=self.request.user)
            .select_related('user', 'post', 'parent__user')
            .annotate(reply_count_value=Count('replies'))
        )

class MyCommentDeleteView(generics.DestroyAPIView):
    queryset = Comments.objects.all()
    serializer_class = CommentSerializer
    permission_classes = (SiteIsAvailable, IsAuthenticated, IsOwner)


class MySongSuggestionDeleteView(generics.DestroyAPIView):
    queryset = BellSongSuggestion.objects.all()
    serializer_class = BellSongSuggestionSerializer
    permission_classes = (SiteIsAvailable, IsAuthenticated, IsOwner)

    def destroy(self, request, *args, **kwargs):
        with transaction.atomic():
            instance = get_object_or_404(
                BellSongSuggestion.objects.select_for_update(),
                pk=kwargs['pk'],
            )
            self.check_object_permissions(request, instance)
            if instance.status != 'pending':
                return Response({"detail": "Можете да триете само чакащи предложения."}, status=status.HTTP_403_FORBIDDEN)
            self.perform_destroy(instance)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MyMemeDeleteView(generics.DestroyAPIView):
    queryset = MemeOfWeek.objects.all()
    serializer_class = MemeOfWeekSerializer
    permission_classes = (SiteIsAvailable, IsAuthenticated, IsOwner)

    def destroy(self, request, *args, **kwargs):
        with transaction.atomic():
            instance = get_object_or_404(
                MemeOfWeek.objects.select_for_update(),
                pk=kwargs['pk'],
            )
            self.check_object_permissions(request, instance)
            if instance.status != 'pending':
                return Response({"detail": "Можете да триете само чакащи мемета."}, status=status.HTTP_403_FORBIDDEN)
            self.perform_destroy(instance)
        return Response(status=status.HTTP_204_NO_CONTENT)


class BellSongSuggestionCreateAPIView(generics.CreateAPIView):
    queryset = BellSongSuggestion.objects.all()
    serializer_class = BellSongSuggestionSerializer
    permission_classes = [SiteIsAvailable, IsAuthenticated]
    throttle_scope = 'song_submit'
    def perform_create(self, serializer):
        site_settings, created = SiteSettings.objects.get_or_create(pk=1)
        if not site_settings.enable_bell_suggestions:
            raise PermissionDenied("Функцията 'Предложения за звънец' в момента е деактивирана.")
        serializer.save(user=self.request.user, status='pending')
class ApprovedBellSongListView(generics.ListAPIView):
    serializer_class = BellSongSuggestionSerializer
    permission_classes = [SiteIsAvailable, AllowAny]

    def get_queryset(self):
        site_settings = SiteSettings.objects.filter(pk=1).first()
        if site_settings and not site_settings.enable_bell_suggestions:
            return BellSongSuggestion.objects.none()
        queryset = (
            BellSongSuggestion.objects.filter(status='approved')
            .select_related('user')
            .order_by('-votes', '-submitted_at')
        )
        return _with_vote_status(
            queryset, self.request, BellSongSuggestion.voted_by
        )

    def get_serializer_context(self):
        return {'request': self.request}
class BellSongVoteView(APIView):
    permission_classes = [SiteIsAvailable, IsAuthenticated]
    throttle_scope = 'vote'

    def post(self, request, pk):
        site_settings = SiteSettings.objects.filter(pk=1).first()
        if site_settings and not site_settings.enable_bell_suggestions:
            return Response(
                {'detail': "Функцията 'Предложения за звънец' е деактивирана."},
                status=status.HTTP_404_NOT_FOUND,
            )
        with transaction.atomic():
            song = get_object_or_404(
                BellSongSuggestion.objects.select_for_update(),
                pk=pk,
            )
            if song.status != 'approved':
                return Response({'detail': 'Може да гласувате само за одобрени песни.'}, status=status.HTTP_400_BAD_REQUEST)
            if song.voted_by.filter(pk=request.user.pk).exists():
                return Response({'detail': 'Вече сте гласували за тази песен.'}, status=status.HTTP_400_BAD_REQUEST)
            song.voted_by.add(request.user)
            song.refresh_from_db(fields=['votes'])

        # Pass context to serializer to access the request
        serializer = BellSongSuggestionSerializer(song, context={'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)


class MemeVoteView(APIView):
    permission_classes = [SiteIsAvailable, IsAuthenticated]
    throttle_scope = 'vote'

    def post(self, request, pk):
        site_settings = SiteSettings.objects.filter(pk=1).first()
        if site_settings and not site_settings.enable_meme_of_the_week:
            return Response(
                {'detail': "Функцията 'Меме на седмицата' е деактивирана."},
                status=status.HTTP_404_NOT_FOUND,
            )
        with transaction.atomic():
            meme = get_object_or_404(
                MemeOfWeek.objects.select_for_update(),
                pk=pk,
            )
            if meme.status != 'approved':
                return Response({'detail': 'Може да гласувате само за одобрени мемета.'}, status=status.HTTP_400_BAD_REQUEST)
            if meme.voted_by.filter(pk=request.user.pk).exists():
                return Response({'detail': 'Вече сте гласували за това меме.'}, status=status.HTTP_400_BAD_REQUEST)
            meme.voted_by.add(request.user)
            meme.refresh_from_db(fields=['votes'])

        # Pass context to serializer to access the request
        serializer = MemeOfWeekSerializer(meme, context={'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)


class CommentList(generics.ListAPIView):
    serializer_class = CommentSerializer
    permission_classes = [SiteIsAvailable, AllowAny]
    http_method_names = ['get', 'post', 'options', 'head']

    def get_queryset(self):
        post_id = self.kwargs.get('post_pk')
        if not post_id:
            return Comments.objects.none()
        post = get_object_or_404(
            Posts,
            id=post_id,
            published=True,
            allowed=True,
        )
        if post.published_at and post.published_at > timezone.now():
            raise Http404
        # Fetch all comments for the post, ordered by creation date.
        # The frontend will be responsible for grouping replies.
        return (
            Comments.objects.filter(post=post)
            .select_related(
                'user',
                'user__userprofile',
                'parent__user',
                'parent__user__userprofile',
            )
            .annotate(reply_count_value=Count('replies'))
            .order_by('created_at')
        )


class AddCommentAPIView(APIView):
    permission_classes = [SiteIsAvailable, IsAuthenticated]

    def post(self, request, post_pk):
        user = request.user
        post = get_object_or_404(
            Posts,
            id=post_pk,
            published=True,
            allowed=True,
        )
        if post.published_at and post.published_at > timezone.now():
            raise Http404

        with transaction.atomic():
            # Locking the user serializes cooldown/count checks for this author.
            locked_user = User.objects.select_for_update().get(pk=user.pk)
            now = timezone.now()

            # Cooldown: 60 seconds between comments
            last_comment = Comments.objects.filter(
                user=locked_user
            ).order_by('-created_at').first()
            if last_comment:
                time_since_last_comment = now - last_comment.created_at
                if time_since_last_comment < timedelta(seconds=60):
                    wait_time = 60 - int(time_since_last_comment.total_seconds())
                    return Response(
                        {"detail": f"Трябва да изчакате още {wait_time} секунди, преди да коментирате отново."},
                        status=status.HTTP_429_TOO_MANY_REQUESTS
                    )

            # Rate Limit: 5 comments per 30 minutes
            thirty_minutes_ago = now - timedelta(minutes=30)
            comment_count = Comments.objects.filter(
                user=locked_user,
                created_at__gte=thirty_minutes_ago,
            ).count()
            if comment_count >= 5:
                return Response(
                    {"detail": "Надвишихте лимита от 5 коментара за 30 минути."},
                    status=status.HTTP_429_TOO_MANY_REQUESTS
                )

            serializer = CommentSerializer(
                data=request.data,
                context={'request': request, 'post': post},
            )
            if serializer.is_valid():
                comment = serializer.save(user=locked_user, post=post)
                return Response(
                    CommentSerializer(comment).data,
                    status=status.HTTP_201_CREATED,
                )
            return Response(
                serializer.errors,
                status=status.HTTP_400_BAD_REQUEST,
            )
class WeeklyPollViewSet(viewsets.ViewSet):
    permission_classes = [SiteIsAvailable, IsAuthenticated]

    def get_throttles(self):
        self.throttle_scope = 'vote' if self.action == 'submit' else None
        return super().get_throttles()

    def get_serializer_class(self):
        if self.action == 'submit':
            return PollAnswerSerializer
        elif self.action == 'statistics':
            return PollStatisticsSerializer
        return UserPollStatusSerializer

    @action(detail=False, methods=['get'])
    def status(self, request):
        site_settings, created = SiteSettings.objects.get_or_create(pk=1)
        if not site_settings.enable_weekly_poll:
            return Response({"detail": "Функцията 'Анкети' в момента е деактивирана."}, status=status.HTTP_404_NOT_FOUND)

        user = request.user
        now = timezone.now()

        active_question = (
            PollQuestion.objects.filter(start_date__lte=now, end_date__gte=now)
            .prefetch_related('options')
            .first()
        )

        if not active_question:
            return Response({"detail": "В момента няма активни въпроси за анкети."}, status=status.HTTP_404_NOT_FOUND)

        latest_answer = (
            PollAnswer.objects.filter(user=user, question=active_question)
            .select_related('selected_option')
            .first()
        )

        if latest_answer:
            correct_option = PollOption.objects.filter(question=active_question, is_correct=True).first()
            data = {
                'is_locked': True,
                'unlocks_at': active_question.end_date,
                'question': active_question,
                'last_result': {
                    'questionId': active_question.id,
                    'selected': latest_answer.selected_option.key,
                    'correct': correct_option.key if correct_option else None
                }
            }
            return Response(UserPollStatusSerializer(data).data)
        else:
            data = {
                'is_locked': False,
                'unlocks_at': None,
                'question': active_question,
                'last_result': None
            }
            return Response(UserPollStatusSerializer(data).data)

    @action(detail=False, methods=['post'])
    def submit(self, request):
        site_settings, created = SiteSettings.objects.get_or_create(pk=1)
        if not site_settings.enable_weekly_poll:
            return Response({"detail": "Функцията 'Анкети' в момента е деактивирана."}, status=status.HTTP_403_FORBIDDEN)

        user = request.user

        serializer = PollAnswerSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        question = serializer.validated_data['question']
        selected_option = serializer.validated_data['selected_option']

        try:
            with transaction.atomic():
                active_question = (
                    PollQuestion.objects.select_for_update()
                    .prefetch_related('options')
                    .filter(id=question.id)
                    .first()
                )

                locked_now = timezone.now()
                if (
                    not active_question
                    or active_question.start_date > locked_now
                    or active_question.end_date < locked_now
                ):
                    return Response({"detail": "Тази анкета в момента не е активна."}, status=status.HTTP_403_FORBIDDEN)

                selected_option = (
                    PollOption.objects.filter(
                        pk=selected_option.pk,
                        question=active_question,
                    )
                    .first()
                )
                if not selected_option:
                    return Response({"detail": "Избраната опция не принадлежи към този въпрос."}, status=status.HTTP_400_BAD_REQUEST)

                PollAnswer.objects.create(
                    user=user,
                    question=active_question,
                    selected_option=selected_option,
                )
        except (IntegrityError, ValidationError):
            return Response(
                {"detail": "Вече сте отговорили на тази анкета."},
                status=status.HTTP_409_CONFLICT,
            )

        correct_option = PollOption.objects.filter(question=active_question, is_correct=True).first()
        data = {
            'is_locked': True,
            'unlocks_at': active_question.end_date,
            'question': active_question,
            'last_result': {
                'questionId': active_question.id,
                'selected': selected_option.key,
                'correct': correct_option.key if correct_option else None
            }
        }
        return Response(UserPollStatusSerializer(data).data, status=status.HTTP_201_CREATED)

    @action(
        detail=False,
        methods=['get'],
        permission_classes=[SiteIsAvailable, AllowAny],
    )
    def statistics(self, request):
        site_settings = SiteSettings.objects.filter(pk=1).first()
        if site_settings and not site_settings.enable_weekly_poll:
            return Response(
                {"detail": "Функцията 'Анкети' в момента е деактивирана."},
                status=status.HTTP_404_NOT_FOUND,
            )
        aggregates = PollAnswer.objects.aggregate(
            total_participants=Count('user', distinct=True),
            total_answers=Count('id'),
            correct_answers=Count(
                'id',
                filter=Q(selected_option__is_correct=True),
            ),
        )
        aggregates['leaderboard'] = list(
            User.objects.filter(
                is_active=True,
                pollanswer__selected_option__is_correct=True,
            )
            .annotate(
                correct_answers=Count(
                    'pollanswer',
                    filter=Q(pollanswer__selected_option__is_correct=True),
                )
            )
            .order_by('-correct_answers', 'username', 'id')
            .values('id', 'username', 'correct_answers')[:5]
        )
        serializer = PollStatisticsSerializer(aggregates)
        return Response(serializer.data)


class ContactFormSubmitView(APIView):
    permission_classes = [SiteIsAvailable, AllowAny]
    throttle_scope = 'contact'
    def post(self, request):
        serializer = ContactSubmissionSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(user=request.user if request.user.is_authenticated else None)
            return Response({"detail": "Съобщението е изпратено успешно!"}, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
class NotificationListView(generics.ListAPIView):
    queryset = Notification.objects.filter(enabled=True).order_by('-created_at')
    serializer_class = NotificationSerializer
    permission_classes = [SiteIsAvailable, AllowAny]

class EventListView(generics.ListAPIView):
    queryset = Event.objects.filter(published=True).order_by('-start_datetime')
    serializer_class = EventSerializer
    permission_classes = [SiteIsAvailable, AllowAny]


class ProgramScheduleView(APIView):
    """Public schedule API. The shape deliberately matches the former CSV columns."""

    permission_classes = [SiteIsAvailable, AllowAny]

    def get(self, request):
        site_settings, _ = SiteSettings.objects.get_or_create(pk=1)
        if not site_settings.enable_program_page:
            return Response(
                {"detail": "Страницата с учебната програма в момента е деактивирана."},
                status=status.HTTP_404_NOT_FOUND,
            )

        entries = ScheduleEntry.objects.all().order_by('class_name', 'lesson_number')
        serialized_entries = ScheduleEntrySerializer(entries, many=True).data
        rows = [
            {
                'Клас': entry['class_name'],
                'Час': str(entry['lesson_number']),
                'Понеделник': entry['monday'],
                'Вторник': entry['tuesday'],
                'Сряда': entry['wednesday'],
                'Четвъртък': entry['thursday'],
                'Петък': entry['friday'],
            }
            for entry in serialized_entries
        ]
        def class_sort_key(class_name):
            match = re.match(r'^\s*(\d+)\s*(.*)$', class_name)
            if match:
                return int(match.group(1)), match.group(2).casefold()
            return 999, class_name.casefold()

        classes = sorted(
            {entry['class_name'] for entry in serialized_entries},
            key=class_sort_key,
        )
        return Response({
            'classes': classes,
            'headers': ['Час', 'Понеделник', 'Вторник', 'Сряда', 'Четвъртък', 'Петък'],
            'rows': rows,
        })

class ChangelogListView(generics.ListAPIView):
    queryset = Changelog.objects.filter(is_active=True).order_by('-updated_at')
    serializer_class = ChangelogSerializer
    permission_classes = [SiteIsAvailable, AllowAny]

class TermsOfServiceView(generics.GenericAPIView):
    serializer_class = TermsOfServiceSerializer
    permission_classes = [SiteIsAvailable, AllowAny]
    def get(self, request):
        tos = TermsOfService.objects.order_by('-date').first()
        if tos:
            return Response(self.get_serializer(tos).data)
        return Response({"detail": "Няма намерени Условия за ползване."}, status=status.HTTP_404_NOT_FOUND)
class PrivacyPolicyView(generics.GenericAPIView):
    serializer_class = PrivacyPolicySerializer
    permission_classes = [SiteIsAvailable, AllowAny]
    def get(self, request):
        pp = PrivacyPolicy.objects.order_by('-date').first()
        if pp:
            return Response(self.get_serializer(pp).data)
        return Response({"detail": "Няма намерена Политика за поверителност."}, status=status.HTTP_404_NOT_FOUND)
class ConsentRecordCreateView(generics.CreateAPIView):
    queryset = ConsentRecord.objects.all()
    serializer_class = ConsentRecordSerializer
    permission_classes = [AllowAny]
    throttle_scope = 'consent'

    def perform_create(self, serializer):
        user = self.request.user if self.request.user.is_authenticated else None
        ip_address = get_client_ip(self.request)
        network_operator, network_asn = lookup_network_operator(ip_address)
        serializer.save(
            user=user,
            ip_address=ip_address,
            network_operator=network_operator,
            network_asn=network_asn,
        )
class SiteStatusView(generics.RetrieveAPIView):
    permission_classes = [AllowAny]
    serializer_class = SiteSettingsSerializer

    def get_object(self):
        obj, created = SiteSettings.objects.get_or_create(pk=1)
        return obj
class CheckUsernameView(APIView):
    permission_classes = [SiteIsAvailable, AllowAny]
    throttle_scope = 'username_check'

    def get(self, request, *args, **kwargs):
        username = request.query_params.get('username', None)
        if not username:
            return Response({'error': 'Username parameter is required.'}, status=status.HTTP_400_BAD_REQUEST)

        username = username.strip()
        try:
            User._meta.get_field('username').clean(username, None)
        except ValidationError as exc:
            return Response(
                {'is_available': False, 'errors': exc.messages},
                status=status.HTTP_400_BAD_REQUEST,
            )
        is_available = not User.objects.filter(username__iexact=username).exists()
        return Response({'is_available': is_available})
class ValidatePasswordView(APIView):
    permission_classes = [SiteIsAvailable, AllowAny]
    throttle_scope = 'password_validate'

    def post(self, request, *args, **kwargs):
        password = request.data.get('password', '')
        try:
            password_validation.validate_password(password)
            return Response({'is_valid': True})
        except ValidationError as e:
            return Response({'is_valid': False, 'errors': e.messages}, status=status.HTTP_400_BAD_REQUEST)


def hqs(request, video_name):
    try:
        # Променяме само името на твоя шаблон, ако е различно
        return render(request, 'hqs.html', {
            'video_url': f'/files/films/{video_name}/index.m3u8'
        })
    except Exception as e:
        # Ако има грешка, ще я видиш в браузъра вместо 500
        return HttpResponse(f"Грешка в Django: {str(e)}")
