import mimetypes
import json
import secrets

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
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.exceptions import (
    PermissionDenied,
    ValidationError as RestValidationError,
)
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.html import escape, strip_tags
from django.utils.text import Truncator
from datetime import datetime, timedelta
import re
from urllib.parse import urljoin
from django.db.models import BooleanField, Count, DateTimeField, Exists, F, OuterRef, Prefetch, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.contrib.auth.models import User
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from .models import Posts, Comments, PollQuestion, PollAnswer, PollOption, ContactSubmission, Notification, Event, TermsOfService, BellSongSuggestion, PrivacyPolicy, MemeOfWeek, ConsentRecord, SiteSettings, Changelog, ScheduleEntry, BlogLoginSession, UserProfile, Badge, PointTransaction, UserBadge, UserDigitalReward, NotificationPreference, NotificationReadState, PageView, PushSubscription, MemeWeekResult, meme_week_start
from .permissions import IsOwner, SiteIsAvailable
from .network import get_client_ip
from .network_operator import lookup_network_operator
from .serializer import (
    PostSerializer, RegisterSerializer, CommentSerializer,
    PollQuestionSerializer, UserPollStatusSerializer, PollAnswerSerializer,
    PollStatisticsSerializer, ContactSubmissionSerializer, NotificationSerializer,
    NotificationPreferenceSerializer, PushSubscriptionSerializer, PageViewCreateSerializer,
    EventSerializer, TermsOfServiceSerializer, BellSongSuggestionSerializer,
    PrivacyPolicySerializer, MemeOfWeekSerializer, ConsentRecordSerializer, SiteSettingsSerializer,
    ChangelogSerializer, PasswordChangeSerializer, UsernameChangeSerializer, ScheduleEntrySerializer, BlogLoginSessionSerializer,
    ApiDetailResponseSerializer, BlogSessionRevokeOthersResponseSerializer, PersonalDataExportSchemaSerializer,
    CurrentUserSerializer, ProfileAvatarUpdateSerializer,
    CurrentProfileResponseSerializer, AvatarUpdateResponseSerializer,
    RegistrationResponseSerializer, EmailVerificationResendRequestSerializer,
    GamificationProfileResponseSerializer, PointTransactionSerializer,
    GamificationActivitySerializer, LeaderboardPreferenceSerializer,
    DigitalRewardSelectionSerializer,
    PublicProfileResponseSerializer, PublicProfileSettingsSerializer,
)
from .gamification.services import (
    active_missions_for_user,
    award_activity,
    award_tracked_activity,
    DigitalRewardSelectionLimitError,
    level_state,
    record_mission_activity,
    select_digital_reward,
)
from .profile_stats import lifetime_activity_counts
from .avatars import (
    avatar_options_payload,
    reset_profile_avatar,
    set_profile_avatar,
    set_profile_preset,
)
from .personal_data_export import build_personal_data_export
from .email_verification import (
    DAILY_QUOTA_EXCEEDED,
    MONTHLY_QUOTA_EXCEEDED,
    RATE_LIMIT_EXCEEDED,
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
from .site_roles import get_site_role
from .notification_categories import (
    CONTENT_CHANGELOG,
    CONTENT_EVENT,
    CONTENT_POLL,
    CONTENT_MEME,
    CONTENT_POST,
    EVENT_REMINDER,
    COMMENT_REPLY,
    MISSION_AVAILABLE,
    SECURITY_ACCOUNT,
    SECURITY_LOGIN,
    SUBMISSION_STATUS,
    PUBLIC_PUSH_CATEGORIES,
)
from .automatic_notifications import create_notification_once
from .notification_sources import notification_target_is_available
from .notification_retention import (
    active_notification_q,
    expired_notification_q,
    notification_is_expired,
)
from .push import (
    cancel_notification_deliveries_for_user,
    cancel_read_deliveries_for_subscription,
)
from .post_analytics import with_current_month_views
from .meme_weeks import latest_winner_result, week_end as meme_week_end
from .public_content import (
    event_search_q,
    post_search_q,
    public_events_queryset,
    public_posts_queryset as _public_posts_queryset,
)


def _post_social_metadata(post):
    canonical_url = f'{settings.PUBLIC_SITE_URL}/post/{post.pk}'
    image_url = (
        urljoin(f'{settings.PUBLIC_SITE_URL}/', post.banner.url.lstrip('/'))
        if post.banner and hasattr(post.banner, 'url')
        else f'{settings.PUBLIC_SITE_URL}/social-preview.jpg'
    )
    return {
        'canonical_url': canonical_url,
        'image_url': image_url,
        'title': f'{post.title} | ПГКНМА Блог',
        'description': post.hook or 'Новини и събития от ПГКНМА.',
    }


def _post_social_head(post):
    metadata = _post_social_metadata(post)
    published_at = post.published_at or post.created_at
    image_type = mimetypes.guess_type(metadata['image_url'])[0] or 'image/jpeg'
    return f'''  <title>{escape(metadata['title'])}</title>
  <meta name="description" content="{escape(metadata['description'])}">
  <link rel="canonical" href="{escape(metadata['canonical_url'])}">
  <meta property="og:title" content="{escape(metadata['title'])}">
  <meta property="og:description" content="{escape(metadata['description'])}">
  <meta property="og:type" content="article">
  <meta property="og:url" content="{escape(metadata['canonical_url'])}">
  <meta property="og:site_name" content="ПГКНМА Блог">
  <meta property="og:locale" content="bg_BG">
  <meta property="og:image" content="{escape(metadata['image_url'])}">
  <meta property="og:image:secure_url" content="{escape(metadata['image_url'])}">
  <meta property="og:image:type" content="{escape(image_type)}">
  <meta property="og:image:alt" content="{escape(post.title)}">
  <meta property="article:published_time" content="{published_at.isoformat()}">
  <meta property="article:section" content="{escape(post.categories.first().full_name if post.categories.first() else '')}">
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="{escape(metadata['title'])}">
  <meta name="twitter:description" content="{escape(metadata['description'])}">
  <meta name="twitter:image" content="{escape(metadata['image_url'])}">
  <meta name="twitter:image:alt" content="{escape(post.title)}">'''


_SOCIAL_HEAD_PATTERNS = (
    re.compile(r'<title\b[^>]*>.*?</title>\s*', re.IGNORECASE | re.DOTALL),
    re.compile(r'<meta\s+name=["\']description["\'][^>]*>\s*', re.IGNORECASE),
    re.compile(r'<link\s+rel=["\']canonical["\'][^>]*>\s*', re.IGNORECASE),
    re.compile(r'<meta\s+property=["\'](?:og:|article:)[^"\']+["\'][^>]*>\s*', re.IGNORECASE),
    re.compile(r'<meta\s+name=["\']twitter:[^"\']+["\'][^>]*>\s*', re.IGNORECASE),
)


def post_page(request, pk):
    """Serve the React app with crawler-visible metadata for one public post."""
    # ``categories`` is a ManyToManyField, so it must be prefetched rather
    # than passed to select_related() (which only supports FK/O2O fields).
    post = get_object_or_404(
        _public_posts_queryset().prefetch_related('categories'),
        pk=pk,
    )
    try:
        html = settings.FRONTEND_INDEX_PATH.read_text(encoding='utf-8')
    except (OSError, UnicodeError):
        return HttpResponse(
            'Frontend build is not available.',
            status=503,
            content_type='text/plain; charset=utf-8',
        )

    for pattern in _SOCIAL_HEAD_PATTERNS:
        html = pattern.sub('', html)
    social_head = _post_social_head(post)
    html, replacements = re.subn(
        r'</head>',
        f'{social_head}\n</head>',
        html,
        count=1,
        flags=re.IGNORECASE,
    )
    if replacements != 1:
        return HttpResponse(
            'Frontend build is invalid.',
            status=503,
            content_type='text/plain; charset=utf-8',
        )

    response = HttpResponse(html, content_type='text/html; charset=utf-8')
    response['Cache-Control'] = 'public, max-age=60, stale-while-revalidate=300'
    return response


MEME_WEEKS_PAGE_SIZE = 12


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
        equipped_rewards = UserDigitalReward.objects.filter(
            selected=True,
            reward__active=True,
        ).select_related('reward')
        return with_current_month_views(
            _public_posts_queryset()
            .select_related('author', 'author__userprofile')
            .prefetch_related(
                'images',
                'documents',
                'categories',
                Prefetch(
                    'author__digital_rewards',
                    queryset=equipped_rewards,
                    to_attr='prefetched_equipped_rewards',
                ),
            )
        )

    @action(
        detail=True,
        methods=['get'],
        url_path='share-preview',
        permission_classes=[AllowAny],
    )
    def share_preview(self, request, pk=None):
        """Return crawler-friendly metadata, then send readers to the SPA post."""
        post = self.get_object()
        metadata = _post_social_metadata(post)
        canonical_url = metadata['canonical_url']
        image_url = metadata['image_url']
        title = metadata['title']
        description = metadata['description']

        html = f'''<!doctype html>
<html lang="bg">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <meta name="description" content="{escape(description)}">
  <meta name="robots" content="noindex, follow">
  <link rel="canonical" href="{escape(canonical_url)}">
  <meta property="og:title" content="{escape(title)}">
  <meta property="og:description" content="{escape(description)}">
  <meta property="og:type" content="article">
  <meta property="og:url" content="{escape(canonical_url)}">
  <meta property="og:site_name" content="ПГКНМА Блог">
  <meta property="og:locale" content="bg_BG">
  <meta property="og:image" content="{escape(image_url)}">
  <meta property="og:image:secure_url" content="{escape(image_url)}">
  <meta property="og:image:alt" content="{escape(post.title)}">
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="{escape(title)}">
  <meta name="twitter:description" content="{escape(description)}">
  <meta name="twitter:image" content="{escape(image_url)}">
  <meta http-equiv="refresh" content="0;url={escape(canonical_url)}">
</head>
<body>
  <p>Отваряне на <a href="{escape(canonical_url)}">{escape(post.title)}</a>…</p>
  <script>window.location.replace({json.dumps(canonical_url)});</script>
</body>
</html>'''
        response = HttpResponse(html, content_type='text/html; charset=utf-8')
        response['Cache-Control'] = 'public, max-age=300, stale-while-revalidate=86400'
        response['X-Robots-Tag'] = 'noindex, follow'
        return response


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
            .order_by('-votes', 'created_at', 'pk')
        )
        if self.action == 'list':
            # The list is one week: the running one, or a past one with ?week=YYYY-MM-DD.
            queryset = queryset.filter(week_start=self._requested_week())
        return _with_vote_status(queryset, self.request, MemeOfWeek.voted_by)

    def _requested_week(self):
        raw = str(self.request.query_params.get('week', '')).strip()
        if raw:
            try:
                return meme_week_start(datetime.strptime(raw, '%Y-%m-%d').date())
            except ValueError:
                raise RestValidationError({'week': 'Очаква се дата във формат ГГГГ-ММ-ДД.'})
        return meme_week_start()

    @action(detail=False, methods=['get'], url_path='weeks')
    def weeks(self, request):
        """Archive of closed weeks with their winners (newest first), plus the running week."""
        site_settings = SiteSettings.objects.filter(pk=1).first()
        if site_settings and not site_settings.enable_meme_of_the_week:
            return Response({'current_week_start': None, 'voting_ends_at': None, 'results': [], 'has_more': False})
        current = meme_week_start()
        results = MemeWeekResult.objects.select_related('winner', 'winner__user').order_by('-week_start')
        before = str(request.query_params.get('before', '')).strip()
        if before:
            try:
                results = results.filter(week_start__lt=datetime.strptime(before, '%Y-%m-%d').date())
            except ValueError:
                raise RestValidationError({'before': 'Очаква се дата във формат ГГГГ-ММ-ДД.'})
        page = list(results[:MEME_WEEKS_PAGE_SIZE + 1])
        serializer_context = self.get_serializer_context()
        payload = []
        for result in page[:MEME_WEEKS_PAGE_SIZE]:
            winner = result.winner if result.winner_id and result.winner and result.winner.status == 'approved' else None
            payload.append({
                'week_start': result.week_start.isoformat(),
                'meme_count': result.meme_count,
                'winner': MemeOfWeekSerializer(winner, context=serializer_context).data if winner else None,
                'winner_votes': result.winner_votes,
            })
        return Response({
            'current_week_start': current.isoformat(),
            'voting_ends_at': meme_week_end(current).isoformat(),
            'results': payload,
            'has_more': len(page) > MEME_WEEKS_PAGE_SIZE,
        })

    @action(detail=False, methods=['get'], url_path='winner')
    def winner(self, request):
        """The latest weekly winner, for the home page and the TV screen."""
        site_settings = SiteSettings.objects.filter(pk=1).first()
        result = None
        if not site_settings or site_settings.enable_meme_of_the_week:
            result = latest_winner_result()
        if result is None:
            return Response({'winner': None})
        return Response({
            'week_start': result.week_start.isoformat(),
            'winner_votes': result.winner_votes,
            'winner': MemeOfWeekSerializer(result.winner, context=self.get_serializer_context()).data,
        })

    def get_permissions(self):
        if self.action in ['list', 'retrieve', 'weeks', 'winner']:
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
        elif delivery.failure_reason == DAILY_QUOTA_EXCEEDED:
            detail = (
                'Профилът е създаден, но дневният лимит за изпращане на '
                'имейли е изчерпан. Опитайте „Изпрати отново“ по-късно.'
            )
        elif delivery.failure_reason == MONTHLY_QUOTA_EXCEEDED:
            detail = (
                'Профилът е създаден, но месечният лимит за изпращане на '
                'имейли е изчерпан. Свържете се с администратор.'
            )
        elif delivery.failure_reason == RATE_LIMIT_EXCEEDED:
            detail = (
                'Профилът е създаден, но в момента се изпращат твърде много '
                'писма. Използвайте „Изпрати отново“ след малко.'
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
                'email_failure_reason': delivery.failure_reason,
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
                'public_profile': PublicProfileSettingsSerializer(
                    user.userprofile
                ).data,
            },
            status=status.HTTP_200_OK,
        )


class PublicProfileSettingsView(APIView):
    permission_classes = (SiteIsAvailable, IsAuthenticated)

    @extend_schema(
        operation_id='profile_public_settings_update',
        summary='Настройки за публичната профилна страница',
        tags=['Профил'],
        request=PublicProfileSettingsSerializer,
        responses={200: PublicProfileSettingsSerializer},
    )
    def patch(self, request):
        profile, _ = UserProfile.objects.get_or_create(user=request.user)
        serializer = PublicProfileSettingsSerializer(
            profile,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class PublicUserProfileView(APIView):
    permission_classes = (SiteIsAvailable, AllowAny)

    @extend_schema(
        operation_id='public_user_profile',
        summary='Публичен потребителски профил',
        tags=['Профил'],
        responses={200: PublicProfileResponseSerializer},
    )
    def get(self, request, username):
        user = (
            User.objects.select_related('userprofile')
            .filter(username__iexact=username, is_active=True)
            .order_by('pk')
            .first()
        )
        if not user:
            raise Http404
        profile, _ = UserProfile.objects.get_or_create(user=user)
        is_owner = request.user.is_authenticated and request.user.pk == user.pk
        if not profile.public_profile_enabled and not is_owner:
            raise Http404

        now = timezone.now()
        public_comments = (
            Comments.objects.filter(
                user=user,
                post__published=True,
                post__allowed=True,
            )
            .filter(
                Q(post__published_at__isnull=True)
                | Q(post__published_at__lte=now)
            )
            .select_related('post')
            .order_by('-created_at')
        )
        comments_visible = is_owner or profile.show_comments_on_profile
        earned_badges = list(
            UserBadge.objects.filter(user=user)
            .select_related('badge')
            .order_by('-awarded_at')
        )
        rewards = list(
            UserDigitalReward.objects.filter(user=user, reward__active=True)
            .select_related('reward')
            .order_by('-unlocked_at')
        )
        lifetime_stats = lifetime_activity_counts(user.pk)
        state = level_state(profile.gamification_points)
        recent_comments = []
        if comments_visible:
            for comment in public_comments[:6]:
                content = comment.content.strip()
                if len(content) > 360:
                    content = f'{content[:357].rstrip()}…'
                recent_comments.append({
                    'id': comment.pk,
                    'post_id': comment.post_id,
                    'post_title': comment.post.title,
                    'content': content,
                    'created_at': comment.created_at,
                })

        payload = {
            'user': {
                'id': user.pk,
                'username': user.username,
                'avatar_url': profile.avatar_url,
                'email': (
                    (user.email or None)
                    if is_owner or profile.show_email_on_profile
                    else None
                ),
                'member_since': user.date_joined,
                'site_role': get_site_role(user),
            },
            'is_owner': is_owner,
            'profile_is_public': profile.public_profile_enabled,
            'email_is_public': profile.show_email_on_profile,
            'comments_are_public': profile.show_comments_on_profile,
            'bio': profile.bio,
            'level': state.current,
            'points': profile.gamification_points,
            'badges': [
                {
                    'slug': item.badge.slug,
                    'title': item.badge.title,
                    'description': item.badge.description,
                    'icon': item.badge.icon,
                    'rarity': item.badge.rarity,
                    'awarded_at': item.awarded_at,
                }
                for item in earned_badges
            ],
            'rewards': [
                {
                    'key': item.reward.key,
                    'title': item.reward.title,
                    'kind': item.reward.kind,
                    'config': item.reward.config,
                    'unlocked_at': item.unlocked_at,
                    'selected': item.selected,
                }
                for item in rewards
            ],
            'stats': {
                'comments': lifetime_stats['comment'] if comments_visible else 0,
                'approved_memes': lifetime_stats['meme_approved'],
                'approved_songs': lifetime_stats['song_approved'],
                'poll_answers': lifetime_stats['poll_vote'],
                'badges': len(earned_badges),
                'rewards': len(rewards),
            },
            'recent_comments': recent_comments,
            'comments_visible': comments_visible,
        }
        return Response(PublicProfileResponseSerializer(payload).data)


class GamificationProfileView(APIView):
    permission_classes = (SiteIsAvailable, IsAuthenticated)

    @extend_schema(
        operation_id='gamification_profile',
        summary='Точки, ниво, значки и последни промени',
        tags=['Геймификация'],
        responses={200: GamificationProfileResponseSerializer},
    )
    def get(self, request):
        profile, _ = UserProfile.objects.get_or_create(user=request.user)
        state = level_state(profile.gamification_points)
        earned_rows = {
            item.badge_id: item
            for item in UserBadge.objects.filter(user=request.user).select_related('badge')
        }
        badges = []
        for badge in Badge.objects.filter(active=True).order_by('title'):
            earned = earned_rows.get(badge.pk)
            if badge.hidden and not earned:
                continue
            badges.append({
                'slug': badge.slug,
                'title': badge.title,
                'description': badge.description,
                'icon': badge.icon,
                'rarity': badge.rarity,
                'hidden': badge.hidden,
                'points_reward': badge.points_reward,
                'earned': earned is not None,
                'awarded_at': earned.awarded_at if earned else None,
            })
        payload = {
            'points': profile.gamification_points,
            'level': state.current,
            'next_level': state.next,
            'progress_percent': state.progress_percent,
            'points_to_next': state.points_to_next,
            'leaderboard_opt_in': profile.leaderboard_opt_in,
            'badges': badges,
            'recent_transactions': PointTransaction.objects.filter(
                user=request.user
            )[:10],
            'missions': active_missions_for_user(request.user),
            'rewards': [
                {
                    'key': row.reward.key,
                    'title': row.reward.title,
                    'kind': row.reward.kind,
                    'config': row.reward.config,
                    'unlocked_at': row.unlocked_at,
                    'selected': row.selected,
                }
                for row in UserDigitalReward.objects.filter(
                    user=request.user,
                    reward__active=True,
                ).select_related('reward').order_by('reward__kind', 'reward__title')
            ],
        }
        return Response(GamificationProfileResponseSerializer(payload).data)


class PointTransactionPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = 'page_size'
    max_page_size = 100


class PointTransactionListView(generics.ListAPIView):
    serializer_class = PointTransactionSerializer
    permission_classes = (SiteIsAvailable, IsAuthenticated)
    pagination_class = PointTransactionPagination

    def get_queryset(self):
        return PointTransaction.objects.filter(user=self.request.user)


class GamificationActivityView(APIView):
    permission_classes = (SiteIsAvailable, IsAuthenticated)
    throttle_scope = 'gamification_activity'

    def post(self, request):
        serializer = GamificationActivitySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        action_name = serializer.validated_data['action']
        if action_name == 'post_read':
            post = _public_posts_queryset().filter(
                pk=serializer.validated_data['object_id']
            ).first()
            if not post:
                raise Http404
            reference_id = post.pk
            point_transaction, points_created = award_activity(
                request.user,
                'post_read',
                post,
            )
        else:
            reference_id = timezone.localdate().isoformat()
            point_transaction, points_created = award_tracked_activity(
                request.user,
                'calendar_visit',
                'blog.calendar_visit',
                reference_id,
            )
        completed = record_mission_activity(
            request.user,
            action_name,
            reference_id,
        )
        return Response(
            {
                'recorded': True,
                'points_awarded': point_transaction.points if points_created else 0,
                'completed_mission_ids': completed,
            },
            status=status.HTTP_202_ACCEPTED,
        )


class DigitalRewardSelectionView(APIView):
    permission_classes = (SiteIsAvailable, IsAuthenticated)

    def patch(self, request, reward_key):
        serializer = DigitalRewardSelectionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            row = select_digital_reward(
                request.user,
                reward_key,
                selected=serializer.validated_data['selected'],
            )
        except UserDigitalReward.DoesNotExist:
            raise Http404
        except DigitalRewardSelectionLimitError as exc:
            raise RestValidationError({'detail': str(exc)}) from exc
        return Response({
            'key': row.reward.key,
            'title': row.reward.title,
            'kind': row.reward.kind,
            'config': row.reward.config,
            'unlocked_at': row.unlocked_at,
            'selected': row.selected,
        })


class LeaderboardPreferenceView(APIView):
    permission_classes = (SiteIsAvailable, IsAuthenticated)

    def patch(self, request):
        serializer = LeaderboardPreferenceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        profile, _ = UserProfile.objects.get_or_create(user=request.user)
        profile.leaderboard_opt_in = serializer.validated_data[
            'leaderboard_opt_in'
        ]
        profile.save(update_fields=('leaderboard_opt_in',))
        return Response({
            'leaderboard_opt_in': profile.leaderboard_opt_in,
        })


class GamificationLeaderboardView(APIView):
    permission_classes = (SiteIsAvailable, AllowAny)

    def _avatar_url(self, profile):
        if not profile or not profile.avatar_url:
            return None
        return urljoin(f'{settings.PUBLIC_SITE_URL}/', profile.avatar_url.lstrip('/'))

    def _ranking(self, start_at=None):
        if start_at is None:
            rows = list(
                UserProfile.objects.filter(
                    leaderboard_opt_in=True,
                    gamification_points__gt=0,
                )
                .values(
                    'user_id',
                    'user__username',
                    'user__is_staff',
                    'user__is_superuser',
                    points=F('gamification_points'),
                )
            )
        else:
            rows = list(
                PointTransaction.objects.filter(
                    user__userprofile__leaderboard_opt_in=True,
                    created_at__gte=start_at,
                )
                .values(
                    'user_id',
                    'user__username',
                    'user__is_staff',
                    'user__is_superuser',
                )
                .annotate(points=Sum('points'))
                .filter(points__gt=0)
            )
        rows.sort(key=lambda row: (-row['points'], row['user__username'].lower()))
        profile_map = {
            profile.user_id: profile
            for profile in UserProfile.objects.filter(
                user_id__in=[row['user_id'] for row in rows]
            )
        }
        reward_map = {}
        for owned_reward in UserDigitalReward.objects.filter(
            user_id__in=[row['user_id'] for row in rows],
            reward__active=True,
            selected=True,
        ).select_related('reward'):
            reward_map.setdefault(owned_reward.user_id, []).append({
                'key': owned_reward.reward.key,
                'title': owned_reward.reward.title,
                'kind': owned_reward.reward.kind,
                'config': owned_reward.reward.config,
                'unlocked_at': owned_reward.unlocked_at,
                'selected': True,
            })
        normalized = []
        for index, row in enumerate(rows, start=1):
            normalized.append({
                'rank': index,
                'username': row['user__username'],
                'avatar_url': self._avatar_url(profile_map.get(row['user_id'])),
                'points': row['points'],
                'user_id': row['user_id'],
                'equipped_rewards': reward_map.get(row['user_id'], []),
                'site_role': (
                    'administrator'
                    if row['user__is_superuser']
                    else 'contributor' if row['user__is_staff'] else None
                ),
            })
        return normalized

    def get(self, request):
        today = timezone.localdate()
        week_start_date = today - timedelta(days=today.weekday())
        week_start = timezone.make_aware(
            datetime.combine(week_start_date, datetime.min.time())
        )
        month_start = timezone.make_aware(
            datetime.combine(today.replace(day=1), datetime.min.time())
        )
        rankings = {
            'weekly': self._ranking(week_start),
            'monthly': self._ranking(month_start),
            'all_time': self._ranking(),
        }
        current_user_id = request.user.pk if request.user.is_authenticated else None
        profile = (
            UserProfile.objects.filter(user=request.user).first()
            if request.user.is_authenticated else None
        )
        response = {}
        for key, ranking in rankings.items():
            current = next(
                (row for row in ranking if row['user_id'] == current_user_id),
                None,
            ) if profile and profile.leaderboard_opt_in else None
            response[key] = {
                'top': [
                    {field: value for field, value in row.items() if field != 'user_id'}
                    for row in ranking[:10]
                ],
                'current_user': (
                    {field: value for field, value in current.items() if field != 'user_id'}
                    if current else None
                ),
            }
        return Response(response)


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
                create_notification_once(
                    key=f'password-changed:{self.object.pk}:{timezone.now().isoformat()}',
                    text='Паролата на профила Ви беше променена.',
                    category=SECURITY_ACCOUNT,
                    action_url='/profile?tab=settings',
                    user=self.object,
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
                    old_username = self.object.username
                    self.object.username = new_username
                    self.object.save(update_fields=['username'])
                    create_notification_once(
                        key=f'username-changed:{self.object.pk}:{timezone.now().isoformat()}',
                        text=f'Потребителското име беше променено от „{old_username}“ на „{new_username}“.',
                        category=SECURITY_ACCOUNT,
                        action_url='/profile?tab=settings',
                        user=self.object,
                    )
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
            create_notification_once(
                key=f'account-deactivated:{instance.pk}:{timezone.now().isoformat()}',
                text='Профилът Ви беше деактивиран.',
                category=SECURITY_ACCOUNT,
                action_url='/',
                user=instance,
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
            if meme.week_start != meme_week_start():
                return Response(
                    {'detail': 'Гласуването за тази седмица приключи.', 'code': 'meme_week_closed'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
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
            .values(
                'id',
                'username',
                'correct_answers',
                'is_staff',
                'is_superuser',
            )[:5]
        )
        for entry in aggregates['leaderboard']:
            entry['site_role'] = (
                'administrator'
                if entry.pop('is_superuser')
                else 'contributor' if entry.pop('is_staff') else None
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
NOTIFICATION_PREFERENCE_CATEGORIES = {
    'changelog_enabled': (CONTENT_CHANGELOG,),
    'posts_enabled': (CONTENT_POST,),
    'events_enabled': (CONTENT_EVENT, EVENT_REMINDER),
    'polls_enabled': (CONTENT_POLL,),
    'memes_enabled': (CONTENT_MEME,),
    'comment_replies_enabled': (COMMENT_REPLY,),
    'submissions_enabled': (SUBMISSION_STATUS,),
    'mission_available_enabled': (MISSION_AVAILABLE,),
    'security_enabled': (SECURITY_LOGIN, SECURITY_ACCOUNT),
    'points_enabled': ('gamification_points',),
    'levels_enabled': ('gamification_level',),
    'badges_enabled': ('gamification_badge',),
    'missions_enabled': ('gamification_mission',),
    'rewards_enabled': ('gamification_reward',),
}


def _accessible_notification_queryset(user):
    queryset = Notification.objects.all()
    if not user.is_authenticated:
        return queryset.filter(
            user__isnull=True,
            audience=Notification.Audience.PUBLIC,
            category__in=PUBLIC_PUSH_CATEGORIES,
        )

    queryset = queryset.filter(
        Q(user=user)
        | Q(
            user__isnull=True,
            audience__in=(
                Notification.Audience.PUBLIC,
                Notification.Audience.AUTHENTICATED,
            ),
        )
    )
    return queryset


def _visible_notification_queryset(
    user,
    *,
    apply_preferences=True,
    include_expired=False,
):
    queryset = _accessible_notification_queryset(user).filter(enabled=True)
    if not include_expired:
        queryset = queryset.filter(active_notification_q())
    if not user.is_authenticated:
        return queryset
    if not apply_preferences:
        return queryset

    preferences, _ = NotificationPreference.objects.get_or_create(user=user)
    if not preferences.general_enabled:
        known_non_general = {
            category
            for categories in NOTIFICATION_PREFERENCE_CATEGORIES.values()
            for category in categories
        }
        queryset = queryset.filter(category__in=known_non_general)
    for field_name, categories in NOTIFICATION_PREFERENCE_CATEGORIES.items():
        if not getattr(preferences, field_name):
            queryset = queryset.exclude(category__in=categories)
    return queryset


def _filter_notification_channel(queryset, channel, *, authenticated):
    if channel == 'gamification':
        if not authenticated:
            return queryset.none()
        return queryset.filter(category__startswith='gamification_')
    if channel == 'official':
        return queryset.filter(category='general')
    if channel == 'updates':
        return queryset.exclude(category='general').exclude(
            category__startswith='gamification_',
        )
    if channel == 'general':
        # Backwards compatibility for clients deployed before the three-tab UI.
        return queryset.exclude(category__startswith='gamification_')
    return queryset


class NotificationListView(generics.ListAPIView):
    serializer_class = NotificationSerializer
    permission_classes = [SiteIsAvailable, AllowAny]

    def get_queryset(self):
        user = self.request.user
        show_archived = str(
            self.request.query_params.get('archived', '')
        ).lower() in ('1', 'true', 'yes')
        queryset = _visible_notification_queryset(
            user,
            apply_preferences=not show_archived,
            include_expired=show_archived,
        )
        queryset = _filter_notification_channel(
            queryset,
            self.request.query_params.get('channel'),
            authenticated=user.is_authenticated,
        )
        if user.is_authenticated:
            read_state = NotificationReadState.objects.filter(
                user=user,
                notification_id=OuterRef('pk'),
            )
            queryset = queryset.annotate(
                _read_at=Subquery(
                    read_state.values('read_at')[:1],
                    output_field=DateTimeField(),
                ),
                _archived_at=Subquery(
                    read_state.values('archived_at')[:1],
                    output_field=DateTimeField(),
                ),
            )
            if show_archived:
                queryset = queryset.filter(
                    Q(_archived_at__isnull=False)
                    | expired_notification_q(),
                )
            else:
                queryset = queryset.filter(_archived_at__isnull=True)
        else:
            queryset = queryset.annotate(
                _read_at=Value(None, output_field=DateTimeField()),
                _archived_at=Value(None, output_field=DateTimeField()),
            )
            if show_archived:
                return queryset.none()
        return queryset.order_by('-created_at')


class NotificationTargetView(APIView):
    """Resolve a push destination only while its source still exists."""

    permission_classes = [SiteIsAvailable, AllowAny]

    def get(self, request, pk):
        notification = get_object_or_404(
            _accessible_notification_queryset(request.user),
            pk=pk,
        )
        action_url = notification.action_url or ''
        is_safe_url = (
            (action_url.startswith('/') and not action_url.startswith('//'))
            or action_url.lower().startswith('https://')
        )
        if not is_safe_url or not notification_target_is_available(notification):
            return Response(
                {
                    'available': False,
                    'action_url': '',
                    'detail': 'Това съдържание вече не е налично.',
                },
                status=status.HTTP_410_GONE,
            )
        return Response({
            'available': True,
            'action_url': action_url,
            'detail': '',
        })


class NotificationReadView(APIView):
    permission_classes = [SiteIsAvailable, IsAuthenticated]

    def post(self, request, pk):
        notification = get_object_or_404(
            _visible_notification_queryset(request.user, apply_preferences=False),
            pk=pk,
        )
        state, _ = NotificationReadState.objects.update_or_create(
            user=request.user,
            notification=notification,
            defaults={'read_at': timezone.now()},
        )
        cancel_notification_deliveries_for_user(
            request.user.pk,
            (notification.pk,),
        )
        return Response({'id': notification.pk, 'read_at': state.read_at})


class NotificationReadAllView(APIView):
    permission_classes = [SiteIsAvailable, IsAuthenticated]

    def post(self, request):
        channel = request.data.get('channel')
        if channel not in (None, '', 'official', 'updates', 'general', 'gamification'):
            raise RestValidationError({'channel': 'Невалиден канал за известия.'})

        queryset = _filter_notification_channel(
            _visible_notification_queryset(request.user),
            channel,
            authenticated=True,
        )
        notification_ids = list(queryset.values_list('pk', flat=True))
        existing_ids = set(NotificationReadState.objects.filter(
            user=request.user,
            notification_id__in=notification_ids,
        ).values_list('notification_id', flat=True))
        unread_ids = [pk for pk in notification_ids if pk not in existing_ids]
        read_at = timezone.now()
        states = [
            NotificationReadState(
                user=request.user,
                notification_id=pk,
                read_at=read_at,
            )
            for pk in unread_ids
        ]
        NotificationReadState.objects.bulk_create(states, ignore_conflicts=True)
        cancel_notification_deliveries_for_user(
            request.user.pk,
            notification_ids,
        )
        return Response({'marked_read': len(states)})


class NotificationArchiveView(APIView):
    permission_classes = [SiteIsAvailable, IsAuthenticated]

    def post(self, request, pk):
        notification = get_object_or_404(
            _visible_notification_queryset(request.user, apply_preferences=False),
            pk=pk,
        )
        archived_at = timezone.now()
        state, _ = NotificationReadState.objects.update_or_create(
            user=request.user,
            notification=notification,
            defaults={
                'read_at': archived_at,
                'archived_at': archived_at,
            },
        )
        cancel_notification_deliveries_for_user(
            request.user.pk,
            (notification.pk,),
        )
        return Response({
            'id': notification.pk,
            'is_archived': True,
            'archived_at': state.archived_at,
        })

    def delete(self, request, pk):
        notification = get_object_or_404(
            _visible_notification_queryset(
                request.user,
                apply_preferences=False,
                include_expired=True,
            ),
            pk=pk,
        )
        if notification_is_expired(notification):
            return Response(
                {
                    'detail': (
                        'Срокът на това известие е изтекъл и то не може да '
                        'бъде върнато в камбанката.'
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        NotificationReadState.objects.filter(
            user=request.user,
            notification=notification,
        ).update(archived_at=None)
        return Response({
            'id': notification.pk,
            'is_archived': False,
            'archived_at': None,
        })


class NotificationArchiveReadView(APIView):
    permission_classes = [SiteIsAvailable, IsAuthenticated]

    def post(self, request):
        channel = request.data.get('channel')
        if channel not in (None, '', 'official', 'updates', 'general', 'gamification'):
            raise RestValidationError({'channel': 'Невалиден канал за известия.'})

        queryset = _filter_notification_channel(
            _visible_notification_queryset(request.user),
            channel,
            authenticated=True,
        )
        notification_ids = list(queryset.values_list('pk', flat=True))
        states = NotificationReadState.objects.filter(
            user=request.user,
            notification_id__in=notification_ids,
            archived_at__isnull=True,
        )
        archived_ids = list(states.values_list('notification_id', flat=True))
        archived_at = timezone.now()
        states.update(archived_at=archived_at)
        cancel_notification_deliveries_for_user(
            request.user.pk,
            archived_ids,
        )
        return Response({'archived': len(archived_ids)})


class NotificationPreferenceView(APIView):
    permission_classes = [SiteIsAvailable, IsAuthenticated]

    def get_object(self, user):
        preferences, _ = NotificationPreference.objects.get_or_create(user=user)
        return preferences

    def get(self, request):
        serializer = NotificationPreferenceSerializer(self.get_object(request.user))
        return Response(serializer.data)

    def patch(self, request):
        preferences = self.get_object(request.user)
        serializer = NotificationPreferenceSerializer(
            preferences,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class PushConfigView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        enabled = bool(settings.WEB_PUSH_ENABLED and settings.VAPID_PUBLIC_KEY)
        return Response({
            'enabled': enabled,
            'public_key': settings.VAPID_PUBLIC_KEY if enabled else '',
        })


class PushSubscriptionView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = 'push_subscription'

    preference_fields = (
        'general_enabled',
        'posts_enabled',
        'events_enabled',
        'polls_enabled',
        'memes_enabled',
        'changelog_enabled',
    )

    def _management_token(self, request):
        return str(
            request.headers.get('X-Push-Management-Token')
            or request.data.get('management_token', '')
        ).strip()

    def _can_manage(self, request, subscription, token):
        return bool(
            (request.user.is_authenticated and subscription.user_id == request.user.pk)
            or subscription.management_token_matches(token)
        )

    def _response_data(self, subscription, *, management_token=''):
        data = {
            'subscribed': bool(subscription.active),
            'linked_to_account': bool(subscription.user_id),
            'preferences': {
                field: getattr(subscription, field)
                for field in self.preference_fields
            },
        }
        if management_token:
            data['management_token'] = management_token
        return data

    def get(self, request):
        endpoint = str(request.query_params.get('endpoint', '')).strip()
        if not endpoint:
            return Response({'subscribed': False})
        if len(endpoint) > 2048:
            raise RestValidationError({'endpoint': 'Push endpoint-ът е прекалено дълъг.'})
        subscription = PushSubscription.objects.filter(
            endpoint_hash=PushSubscription.digest_endpoint(endpoint),
            active=True,
        ).first()
        if subscription is None or not self._can_manage(
            request,
            subscription,
            self._management_token(request),
        ):
            return Response({'subscribed': False})
        return Response(self._response_data(subscription))

    def post(self, request):
        if request.data.get('detach') is True:
            return self._detach(request)

        serializer = PushSubscriptionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = dict(serializer.validated_data)
        supplied_token = values.pop('management_token', '')
        endpoint_hash = PushSubscription.digest_endpoint(values['endpoint'])
        subscription = PushSubscription.objects.filter(endpoint_hash=endpoint_hash).first()
        created = subscription is None
        if subscription is None:
            subscription = PushSubscription(endpoint_hash=endpoint_hash)
        elif not self._can_manage(request, subscription, supplied_token):
            # The browser encryption keys prove control of an existing local
            # anonymous subscription and allow recovery after cleared
            # localStorage. Authenticated users must be the current owner or
            # present the device management token before linking it.
            owns_local_subscription = bool(
                not request.user.is_authenticated
                and subscription.p256dh == values['p256dh']
                and subscription.auth == values['auth']
            )
            if not owns_local_subscription:
                raise PermissionDenied('Този push абонамент не може да бъде управляван без валиден токен.')
            subscription.management_token_hash = ''

        management_token = supplied_token
        if not subscription.management_token_hash:
            management_token = secrets.token_urlsafe(32)
            subscription.management_token_hash = PushSubscription.digest_management_token(
                management_token
            )

        subscription.endpoint = values['endpoint']
        subscription.p256dh = values['p256dh']
        subscription.auth = values['auth']
        subscription.user_agent = request.headers.get('User-Agent', '')[:255]
        subscription.active = True
        subscription.failure_count = 0
        if request.user.is_authenticated:
            subscription.user = request.user
        for field in self.preference_fields:
            if field in values:
                setattr(subscription, field, values[field])
        subscription.save()
        if request.user.is_authenticated:
            cancel_read_deliveries_for_subscription(subscription)
        return Response(
            self._response_data(subscription, management_token=management_token),
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    def _detach(self, request):
        endpoint = str(request.data.get('endpoint', '')).strip()
        if not endpoint:
            raise RestValidationError({'endpoint': 'Полето endpoint е задължително.'})
        subscription = PushSubscription.objects.filter(
            endpoint_hash=PushSubscription.digest_endpoint(endpoint),
        ).first()
        if subscription is None:
            return Response({'subscribed': False, 'detached': False})
        supplied_token = self._management_token(request)
        if not self._can_manage(request, subscription, supplied_token):
            raise PermissionDenied('Този push абонамент не може да бъде откачен без валиден токен.')
        management_token = supplied_token or secrets.token_urlsafe(32)
        subscription.management_token_hash = PushSubscription.digest_management_token(
            management_token
        )
        subscription.user = None
        subscription.save(update_fields=('user', 'management_token_hash', 'updated_at'))
        data = self._response_data(subscription, management_token=management_token)
        data['detached'] = True
        return Response(data)

    def delete(self, request):
        endpoint = str(request.data.get('endpoint', '')).strip()
        if not endpoint:
            raise RestValidationError({'endpoint': 'Полето endpoint е задължително.'})
        subscription = PushSubscription.objects.filter(
            endpoint_hash=PushSubscription.digest_endpoint(endpoint),
        ).first()
        if subscription is None:
            return Response({'subscribed': False, 'removed': False})
        if not self._can_manage(request, subscription, self._management_token(request)):
            raise PermissionDenied('Този push абонамент не може да бъде премахнат без валиден токен.')
        subscription.delete()
        return Response({'subscribed': False, 'removed': True})


class GlobalSearchView(APIView):
    permission_classes = [SiteIsAvailable, AllowAny]
    throttle_scope = 'search'

    def get(self, request):
        query = str(request.query_params.get('q', '')).strip()[:100]
        if len(query) < 2:
            return Response({'query': query, 'results': []})

        results = []
        posts = _public_posts_queryset().filter(
            post_search_q(query)
        ).distinct().prefetch_related('categories')[:6]
        for post in posts:
            first_category = post.categories.first()
            meta = first_category.full_name if first_category else ''
            results.append({
                'kind': 'post',
                'id': post.pk,
                'title': post.title,
                'description': Truncator(strip_tags(post.hook or post.content)).chars(140),
                'url': f'/post/{post.pk}',
                'image_url': post.banner.url if post.banner else '',
                'meta': meta,
            })

        events = public_events_queryset().filter(
            event_search_q(query)
        ).order_by('start_datetime')[:5]
        for event in events:
            results.append({
                'kind': 'event',
                'id': event.pk,
                'title': event.title,
                'description': Truncator(strip_tags(event.description)).chars(140),
                'url': f'/events?event={event.pk}',
                'image_url': '',
                'meta': f'{event.location} · {event.start_datetime.isoformat()}',
            })

        songs = BellSongSuggestion.objects.filter(status='approved').filter(
            Q(title__icontains=query) | Q(note__icontains=query)
        ).order_by('-submitted_at')[:4]
        for song in songs:
            results.append({
                'kind': 'song',
                'id': song.pk,
                'title': song.title,
                'description': Truncator(strip_tags(song.note or 'Одобрено предложение за училищния звънец.')).chars(140),
                'url': '/bell-suggest#approved-songs',
                'image_url': '',
                'meta': song.get_slot_display(),
            })

        memes = MemeOfWeek.objects.filter(status='approved', title__icontains=query).order_by('-created_at')[:4]
        for meme in memes:
            results.append({
                'kind': 'meme',
                'id': meme.pk,
                'title': meme.title or 'Меме на седмицата',
                'description': 'Одобрено меме от училищната общност.',
                'url': '/meme-of-the-week',
                'image_url': '',
                'meta': 'Меме',
            })

        return Response({'query': query, 'results': results})


class PageViewCreateView(generics.CreateAPIView):
    serializer_class = PageViewCreateSerializer
    permission_classes = [SiteIsAvailable, AllowAny]
    throttle_scope = 'analytics'

    def perform_create(self, serializer):
        path = serializer.validated_data['path']
        post = None
        match = re.fullmatch(r'/post/(\d+)/?', path)
        if match:
            post = _public_posts_queryset().filter(pk=int(match.group(1))).first()
        serializer.save(
            user=self.request.user if self.request.user.is_authenticated else None,
            post=post,
        )

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
