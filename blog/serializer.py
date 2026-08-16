from rest_framework import serializers
from .models import Posts, UserProfile, Comments, PollQuestion, PollAnswer, PollOption, ContactSubmission, Notification, \
    Event, TermsOfService, PostImage, BellSongSuggestion, PrivacyPolicy, MemeOfWeek, ConsentRecord, SiteSettings, PostDocument, Changelog, ScheduleEntry, \
    validate_meme_image_size, BlogLoginSession, Badge, DigitalReward, GamificationLevel, PointTransaction, UserBadge, UserDigitalReward, \
    NotificationPreference, PageView, PushSubscription
from .avatars import AVATAR_PRESET_IDS, validate_avatar_upload_size
from django.conf import settings
from django.contrib.auth.models import User, update_last_login
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone
import requests
import re
from django.utils.html import escape
from rest_framework_simplejwt.serializers import (
    TokenObtainPairSerializer,
    TokenObtainSerializer,
)
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework.response import Response
from rest_framework import status
from .network import get_client_ip
from .session_auth import issue_session_tokens
from .site_roles import get_site_role
from .notification_retention import notification_is_expired
from .push_endpoints import UnsafePushEndpoint, validate_web_push_endpoint


def _user_profile(user):
    if not user:
        return None
    try:
        return user.userprofile
    except UserProfile.DoesNotExist:
        return None


def _user_avatar_url(user):
    profile = _user_profile(user)
    return profile.avatar_url if profile else None


def _digital_reward_payload(row):
    return {
        'key': row.reward.key,
        'title': row.reward.title,
        'kind': row.reward.kind,
        'config': row.reward.config,
        'unlocked_at': row.unlocked_at,
        'selected': row.selected,
    }


def _user_equipped_rewards(user):
    if not user:
        return []
    prefetched_rewards = getattr(user, 'prefetched_equipped_rewards', None)
    if prefetched_rewards is not None:
        return [_digital_reward_payload(row) for row in prefetched_rewards]
    return [
        _digital_reward_payload(row)
        for row in UserDigitalReward.objects.filter(
            user=user,
            selected=True,
            reward__active=True,
        ).select_related('reward')
    ]


class CurrentUserSerializer(serializers.ModelSerializer):
    has_password = serializers.SerializerMethodField()
    avatar_url = serializers.SerializerMethodField()
    avatar_source = serializers.SerializerMethodField()
    avatar_preset = serializers.SerializerMethodField()
    email_verified = serializers.SerializerMethodField()
    site_role = serializers.SerializerMethodField()
    can_access_admin = serializers.SerializerMethodField()
    equipped_rewards = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = (
            'id',
            'username',
            'email',
            'has_password',
            'avatar_url',
            'avatar_source',
            'avatar_preset',
            'email_verified',
            'site_role',
            'can_access_admin',
            'equipped_rewards',
        )
        read_only_fields = fields

    def get_has_password(self, obj) -> bool:
        return obj.has_usable_password()

    def get_avatar_url(self, obj) -> str | None:
        return _user_avatar_url(obj)

    def get_avatar_source(self, obj) -> str:
        profile = _user_profile(obj)
        return (
            profile.avatar_source
            if profile
            else UserProfile.AvatarSource.INITIALS
        )

    def get_avatar_preset(self, obj) -> str:
        profile = _user_profile(obj)
        return profile.avatar_preset if profile else ''

    def get_email_verified(self, obj) -> bool:
        profile = _user_profile(obj)
        return profile.email_verified if profile else True

    def get_site_role(self, obj) -> str | None:
        return get_site_role(obj)

    def get_can_access_admin(self, obj) -> bool:
        return bool(obj.is_active and obj.is_staff)

    def get_equipped_rewards(self, obj) -> list[dict]:
        return _user_equipped_rewards(obj)


class ProfileAvatarUpdateSerializer(serializers.Serializer):
    avatar = serializers.ImageField(
        required=False,
        validators=[validate_avatar_upload_size],
    )
    preset_id = serializers.ChoiceField(
        required=False,
        choices=sorted(AVATAR_PRESET_IDS),
    )

    def validate(self, attrs):
        supplied = [key for key in ('avatar', 'preset_id') if key in attrs]
        if len(supplied) != 1:
            raise serializers.ValidationError(
                'Изпратете точно една снимка или един готов аватар.'
            )
        return attrs


class AvatarPresetSerializer(serializers.Serializer):
    id = serializers.CharField()
    label = serializers.CharField()
    url = serializers.CharField()


class AvatarOptionsSerializer(serializers.Serializer):
    max_upload_bytes = serializers.IntegerField()
    output_size = serializers.IntegerField()
    allowed_mime_types = serializers.ListField(
        child=serializers.CharField(),
    )
    presets = AvatarPresetSerializer(many=True)


class PublicProfileSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserProfile
        fields = (
            'bio',
            'public_profile_enabled',
            'show_email_on_profile',
            'show_comments_on_profile',
        )

    def validate_bio(self, value):
        return value.strip()


class CurrentProfileResponseSerializer(serializers.Serializer):
    user = CurrentUserSerializer()
    avatar_options = AvatarOptionsSerializer()
    public_profile = PublicProfileSettingsSerializer()


class AvatarUpdateResponseSerializer(serializers.Serializer):
    user = CurrentUserSerializer()

class PostImageSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = PostImage
        fields = (
            'id',
            'image_url',
            'title',
            'alt_text',
            'position',
            'is_featured',
            'focus_x',
            'focus_y',
        )

    def get_image_url(self, obj) -> str | None:
        if obj.image and hasattr(obj.image, 'url'):
            return obj.image.url
        return None

class PostDocumentSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField()
    file_name = serializers.SerializerMethodField()

    class Meta:
        model = PostDocument
        fields = ['id', 'file_name', 'file_url', 'uploaded_at']

    def get_file_url(self, obj):
        if obj.file and hasattr(obj.file, 'url'):
            return obj.file.url
        return None

    def get_file_name(self, obj):
        if obj.file and hasattr(obj.file, 'name'):
            return obj.file.name.split('/')[-1]
        return None

class PostSerializer(serializers.ModelSerializer):
    author_username = serializers.SerializerMethodField()
    author_avatar_url = serializers.SerializerMethodField()
    author_site_role = serializers.SerializerMethodField()
    author_equipped_rewards = serializers.SerializerMethodField()
    category_name = serializers.StringRelatedField(source='category')
    images = serializers.SerializerMethodField()
    gallery = PostImageSerializer(source='images', many=True, read_only=True)
    documents = serializers.SerializerMethodField()
    banner = serializers.SerializerMethodField()
    views_this_month = serializers.SerializerMethodField()

    class Meta:
        model = Posts
        fields = (
            'id',
            'category',       # Запазваме ID за бъдещи филтрирания
            'category_name',  # ⬅️ Добавено: Име на категория
            'author',         # Запазваме ID на автора
            'author_username',# ⬅️ Добавено: Потребителско име
            'author_avatar_url',
            'author_site_role',
            'author_equipped_rewards',
            'title',
            'banner',
            'hook',
            'content',
            'created_at',
            'published_at',
            'published',
            'allowed',
            'images',
            'gallery',
            'documents',
            'views_this_month',
        )

    def get_banner(self, obj):
        if obj.banner and hasattr(obj.banner, 'url'):
            return obj.banner.url
        return None

    def get_author_username(self, obj):
        return obj.author.username if obj.author else None

    def get_author_avatar_url(self, obj) -> str | None:
        return _user_avatar_url(obj.author)

    def get_author_site_role(self, obj) -> str | None:
        return get_site_role(obj.author)

    def get_author_equipped_rewards(self, obj) -> list[dict]:
        return _user_equipped_rewards(obj.author)

    def get_images(self, obj):
        images = obj.images.all()
        return [image.image.url for image in images if image.image]

    def get_documents(self, obj):
        request = self.context.get('request')
        documents = obj.documents.all()
        return [PostDocumentSerializer(doc, context={'request': request}).data for doc in documents]

    def get_views_this_month(self, obj) -> int:
        return int(getattr(obj, 'views_this_month', 0) or 0)

class RegisterSerializer(serializers.ModelSerializer):
    username = serializers.CharField(
        required=True,
        allow_blank=False,
        max_length=User._meta.get_field('username').max_length,
        trim_whitespace=True,
        validators=[UnicodeUsernameValidator()],
    )
    email = serializers.EmailField(required=True, allow_blank=False)
    first_name = serializers.CharField(
        required=True,
        allow_blank=False,
        max_length=User._meta.get_field('first_name').max_length,
        trim_whitespace=True,
    )
    last_name = serializers.CharField(
        required=True,
        allow_blank=False,
        max_length=User._meta.get_field('last_name').max_length,
        trim_whitespace=True,
    )
    password = serializers.CharField(
        write_only=True,
        required=True,
    )

    password2 = serializers.CharField(
        write_only=True,
        required=True
    )
    
    # Тук вече приемаме само токена от Cloudflare
    captcha_answer = serializers.CharField(write_only=True, required=True)

    class Meta:
        model = User
        fields = (
            'username', 'password', 'password2', 'email', 
            'first_name', 'last_name', 'captcha_answer'
        )
    def validate_username(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Потребителското име е задължително.")
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("Това потребителско име вече е заето.")
        return value

    def validate_email(self, value):
        email = User.objects.normalize_email(value).strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise serializers.ValidationError(
                "Вече съществува акаунт с този имейл адрес."
            )
        return email

    def validate(self, attrs):
        # 1. Проверка на паролите
        if attrs['password'] != attrs['password2']:
            raise serializers.ValidationError({"password": "Паролите не съвпадат."})

        candidate = User(
            username=attrs['username'],
            email=attrs['email'],
            first_name=attrs['first_name'],
            last_name=attrs['last_name'],
        )
        try:
            validate_password(attrs['password'], user=candidate)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({'password': exc.messages})

        # 2. Вземаме токена и Secret Key
        captcha_token = attrs.get('captcha_answer')
        secret_key = getattr(settings, 'TURNSTILE_SECRET_KEY', '').strip()
        verify_url = getattr(
            settings,
            'TURNSTILE_VERIFY_URL',
            'https://challenges.cloudflare.com/turnstile/v0/siteverify',
        )
        if not secret_key:
            raise serializers.ValidationError({
                "captcha": "Защитата срещу ботове не е конфигурирана."
            })

        # 3. Валидация през API-то на Cloudflare
        try:
            request = self.context.get('request')
            remote_ip = get_client_ip(request) if request else None
            payload = {
                'secret': secret_key,
                'response': captcha_token,
            }
            if remote_ip:
                payload['remoteip'] = remote_ip
            verify_response = requests.post(
                verify_url,
                data=payload,
                timeout=5,
            )
            verify_response.raise_for_status()
            result = verify_response.json()

            if not result.get('success'):
                raise serializers.ValidationError({"captcha": "Неуспешна проверка за ботове. Опитайте пак."})

        except (requests.exceptions.RequestException, ValueError):
            raise serializers.ValidationError({"captcha": "Грешка при връзка със защитната система."})

        # Премахваме помощните полета
        attrs.pop('password2')
        attrs.pop('captcha_answer')
        return attrs

    def create(self, validated_data):
        # Serialize all account creation paths on the singleton settings row.
        # This closes the gap between case-insensitive e-mail validation and
        # insert even though Django's built-in User.email is not DB-unique.
        try:
            with transaction.atomic():
                site_settings, _ = (
                    SiteSettings.objects.select_for_update()
                    .get_or_create(pk=1)
                )
                if not site_settings.enable_user_registration:
                    raise serializers.ValidationError({
                        'detail': "Регистрацията временно е деактивирана."
                    })
                if User.objects.filter(
                    email__iexact=validated_data['email']
                ).exists():
                    raise serializers.ValidationError({
                        'email': "Вече съществува акаунт с този имейл адрес."
                    })
                if User.objects.filter(
                    username__iexact=validated_data['username']
                ).exists():
                    raise serializers.ValidationError({
                        'username': "Това потребителско име вече е заето."
                    })
                user = User(
                    username=validated_data['username'],
                    email=validated_data['email'],
                    first_name=validated_data['first_name'],
                    last_name=validated_data['last_name']
                )
                user.set_password(validated_data['password'])
                user.save()

                # Създаваме свързания UserProfile
                profile, _ = UserProfile.objects.get_or_create(user=user)
                profile.email_verified = False
                profile.email_verified_at = None
                profile.save(
                    update_fields=('email_verified', 'email_verified_at')
                )
        except IntegrityError:
            raise serializers.ValidationError({
                'detail': (
                    "Акаунт с тези данни вече съществува. "
                    "Проверете потребителското име и имейла."
                )
            })

        return user


class PasswordChangeSerializer(serializers.Serializer):
    current_password = serializers.CharField(style={"input_type": "password"}, required=True)
    new_password = serializers.CharField(style={"input_type": "password"}, required=True)
    new_password_confirm = serializers.CharField(style={"input_type": "password"}, required=True)

    def validate_new_password(self, value):
        request = self.context.get('request')
        user = request.user if request else None
        validate_password(value, user=user)
        return value

    def validate(self, data):
        if data['new_password'] != data['new_password_confirm']:
            raise serializers.ValidationError({"new_password": "Новата парола и потвърждението не съвпадат."})
        return data


class UsernameChangeSerializer(serializers.Serializer):
    current_password = serializers.CharField(style={"input_type": "password"}, required=True)
    new_username = serializers.CharField(
        required=True,
        allow_blank=False,
        max_length=User._meta.get_field('username').max_length,
        trim_whitespace=True,
        validators=[UnicodeUsernameValidator()],
    )

    def validate_new_username(self, value):
        value = value.strip()
        user = self.context['request'].user
        if User.objects.exclude(pk=user.pk).filter(username__iexact=value).exists():
            raise serializers.ValidationError("Това потребителско име вече е заето.")
        return value





class CommentSerializer(serializers.ModelSerializer):
    content = serializers.CharField(
        required=True,
        allow_blank=False,
        max_length=5000,
        trim_whitespace=True,
    )
    username = serializers.SerializerMethodField()
    avatar_url = serializers.SerializerMethodField()
    site_role = serializers.SerializerMethodField()
    equipped_rewards = serializers.SerializerMethodField()
    post_id = serializers.IntegerField(source='post.id', read_only=True)
    parent_id = serializers.PrimaryKeyRelatedField(source='parent', read_only=True)
    parent_username = serializers.SerializerMethodField()
    parent_site_role = serializers.SerializerMethodField()
    reply_count = serializers.SerializerMethodField()

    class Meta:
        model = Comments
        fields = ['id', 'post_id', 'username', 'avatar_url', 'site_role', 'equipped_rewards', 'content', 'created_at', 'parent', 'parent_id', 'parent_username', 'parent_site_role', 'reply_count']
        extra_kwargs = {
            'parent': {'write_only': True, 'required': False, 'allow_null': True, 'queryset': Comments.objects.all()},
        }
        read_only_fields = ['id', 'created_at', 'username', 'avatar_url', 'site_role', 'equipped_rewards', 'post_id', 'parent_id', 'parent_username', 'parent_site_role', 'reply_count']

    def get_parent_username(self, obj):
        if obj.parent:
            return obj.parent.user.username if obj.parent.user else None
        return None

    def get_parent_site_role(self, obj) -> str | None:
        return get_site_role(obj.parent.user) if obj.parent else None

    def get_username(self, obj):
        return obj.user.username if obj.user else None

    def get_avatar_url(self, obj) -> str | None:
        return _user_avatar_url(obj.user)

    def get_site_role(self, obj) -> str | None:
        return get_site_role(obj.user)

    def get_equipped_rewards(self, obj) -> list[dict]:
        return _user_equipped_rewards(obj.user)

    def get_reply_count(self, obj):
        if obj.parent is None:
            annotated_count = getattr(obj, 'reply_count_value', None)
            if annotated_count is not None:
                return annotated_count
            return obj.replies.count()
        return 0

    def validate_parent(self, parent):
        post = self.context.get('post')
        if post and parent and parent.post_id != post.pk:
            raise serializers.ValidationError(
                "Родителският коментар не принадлежи към тази публикация."
            )
        return parent

    def create(self, validated_data):
        parent = validated_data.get('parent')
        if parent and parent.parent:
            validated_data['parent'] = parent.parent
        
        return super().create(validated_data)



class PollOptionSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = PollOption
        fields = ['id', 'key', 'text', 'image_url']

    def get_image_url(self, obj):
        if obj.image and hasattr(obj.image, 'url'):
            return obj.image.url
        return None

class PollQuestionSerializer(serializers.ModelSerializer):
    options = PollOptionSerializer(many=True, read_only=True)
    id = serializers.IntegerField(read_only=True)
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = PollQuestion
        fields = ['id', 'title', 'subtitle', 'task_description', 'image_url', 'options']

    def get_image_url(self, obj):
        if obj.image and hasattr(obj.image, 'url'):
            return obj.image.url
        return None

class PollAnswerSerializer(serializers.ModelSerializer):
    class Meta:
        model = PollAnswer
        fields = ['question', 'selected_option']

    def validate(self, attrs):
        question = attrs.get('question')
        selected_option = attrs.get('selected_option')
        if (
            question
            and selected_option
            and selected_option.question_id != question.pk
        ):
            raise serializers.ValidationError({
                'selected_option': "Избраната опция не принадлежи към въпроса."
            })
        return attrs


class UserPollStatusSerializer(serializers.Serializer):
    is_locked = serializers.BooleanField()
    unlocks_at = serializers.DateTimeField(allow_null=True)
    last_result = serializers.JSONField(allow_null=True)
    question = PollQuestionSerializer(allow_null=True)


class PollLeaderboardEntrySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    username = serializers.CharField()
    correct_answers = serializers.IntegerField()
    site_role = serializers.CharField(allow_null=True)


class PollStatisticsSerializer(serializers.Serializer):
    total_participants = serializers.IntegerField()
    total_answers = serializers.IntegerField()
    correct_answers = serializers.IntegerField()
    leaderboard = PollLeaderboardEntrySerializer(many=True)


class ContactSubmissionSerializer(serializers.ModelSerializer):
    message = serializers.CharField(
        required=True,
        allow_blank=False,
        max_length=5000,
        trim_whitespace=True,
    )

    class Meta:
        model = ContactSubmission
        fields = ['name', 'email', 'message', 'reason']


class NotificationSerializer(serializers.ModelSerializer):
    html_text = serializers.SerializerMethodField()
    is_gamification = serializers.SerializerMethodField()
    is_read = serializers.SerializerMethodField()
    read_at = serializers.SerializerMethodField()
    is_archived = serializers.SerializerMethodField()
    archived_at = serializers.SerializerMethodField()
    is_auto_archived = serializers.SerializerMethodField()
    can_restore = serializers.SerializerMethodField()

    class Meta:
        model = Notification
        fields = [
            'id', 'text', 'enabled', 'created_at', 'html_text',
            'category', 'action_url',
            'is_gamification', 'is_read', 'read_at',
            'important', 'expires_at',
            'is_archived', 'archived_at', 'is_auto_archived', 'can_restore',
        ]
        read_only_fields = fields

    def get_html_text(self, obj):
        # Notifications are rendered as escaped plaintext. Only line breaks are
        # converted, so Markdown cannot synthesize javascript: links.
        return '<br>'.join(escape(obj.text).splitlines())

    def get_is_gamification(self, obj) -> bool:
        return obj.category.startswith('gamification_')

    def get_is_read(self, obj) -> bool:
        return getattr(obj, '_read_at', None) is not None

    def get_read_at(self, obj):
        return getattr(obj, '_read_at', None)

    def get_is_archived(self, obj) -> bool:
        return (
            getattr(obj, '_archived_at', None) is not None
            or notification_is_expired(obj)
        )

    def get_archived_at(self, obj):
        if notification_is_expired(obj):
            return obj.expires_at
        return getattr(obj, '_archived_at', None)

    def get_is_auto_archived(self, obj) -> bool:
        return notification_is_expired(obj)

    def get_can_restore(self, obj) -> bool:
        return bool(
            getattr(obj, '_archived_at', None) is not None
            and not notification_is_expired(obj)
        )


class NotificationPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationPreference
        fields = (
            'general_enabled',
            'points_enabled',
            'levels_enabled',
            'badges_enabled',
            'missions_enabled',
            'rewards_enabled',
            'posts_enabled',
            'events_enabled',
            'polls_enabled',
            'changelog_enabled',
            'comment_replies_enabled',
            'submissions_enabled',
            'mission_available_enabled',
            'security_enabled',
            'updated_at',
        )
        read_only_fields = ('updated_at',)


class PushSubscriptionSerializer(serializers.ModelSerializer):
    endpoint = serializers.URLField(max_length=2048)
    management_token = serializers.CharField(
        required=False,
        allow_blank=True,
        write_only=True,
        max_length=200,
    )

    class Meta:
        model = PushSubscription
        fields = (
            'endpoint',
            'p256dh',
            'auth',
            'management_token',
            'general_enabled',
            'posts_enabled',
            'events_enabled',
            'polls_enabled',
            'changelog_enabled',
        )

    def validate_endpoint(self, value):
        try:
            return validate_web_push_endpoint(value)
        except UnsafePushEndpoint as exc:
            raise serializers.ValidationError(str(exc)) from exc


class PageViewCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = PageView
        fields = ('path',)

    def validate_path(self, value):
        path = value.strip()
        if not path.startswith('/') or path.startswith('//'):
            raise serializers.ValidationError('Невалиден път на страница.')
        if '?' in path or '#' in path:
            raise serializers.ValidationError('Изпратете път без query параметри и fragment.')
        return path


class GamificationLevelSerializer(serializers.ModelSerializer):
    class Meta:
        model = GamificationLevel
        fields = ('number', 'title', 'icon', 'minimum_points')


class GamificationBadgeSerializer(serializers.ModelSerializer):
    earned = serializers.BooleanField(read_only=True)
    awarded_at = serializers.DateTimeField(read_only=True, allow_null=True)

    class Meta:
        model = Badge
        fields = (
            'slug', 'title', 'description', 'icon', 'rarity', 'hidden',
            'points_reward', 'earned', 'awarded_at',
        )


class PointTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = PointTransaction
        fields = (
            'id', 'action', 'points', 'balance_after', 'description',
            'source', 'created_at',
        )


class GamificationProfileResponseSerializer(serializers.Serializer):
    points = serializers.IntegerField()
    level = GamificationLevelSerializer(allow_null=True)
    next_level = GamificationLevelSerializer(allow_null=True)
    progress_percent = serializers.IntegerField()
    points_to_next = serializers.IntegerField()
    leaderboard_opt_in = serializers.BooleanField()
    badges = GamificationBadgeSerializer(many=True)
    recent_transactions = PointTransactionSerializer(many=True)
    missions = serializers.ListField(child=serializers.DictField())
    rewards = serializers.ListField(child=serializers.DictField())


class GamificationActivitySerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=('post_read', 'calendar_visit'))
    object_id = serializers.IntegerField(required=False, min_value=1)
    engagement_seconds = serializers.IntegerField(
        required=False,
        min_value=0,
        max_value=86400,
    )

    def validate(self, attrs):
        if attrs['action'] == 'post_read':
            if 'object_id' not in attrs:
                raise serializers.ValidationError({
                    'object_id': 'Липсва публикацията, която е прочетена.',
                })
            if attrs.get('engagement_seconds', 0) < 15:
                raise serializers.ValidationError({
                    'engagement_seconds': 'Нужни са поне 15 секунди четене.',
                })
        return attrs


class DigitalRewardSelectionSerializer(serializers.Serializer):
    selected = serializers.BooleanField(default=True)


class LeaderboardPreferenceSerializer(serializers.Serializer):
    leaderboard_opt_in = serializers.BooleanField()


class PublicUserSummarySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    username = serializers.CharField()
    avatar_url = serializers.CharField(allow_null=True)
    email = serializers.EmailField(allow_null=True)
    member_since = serializers.DateTimeField()
    site_role = serializers.CharField(allow_null=True)


class PublicEarnedBadgeSerializer(serializers.Serializer):
    slug = serializers.CharField()
    title = serializers.CharField()
    description = serializers.CharField()
    icon = serializers.CharField()
    rarity = serializers.CharField()
    awarded_at = serializers.DateTimeField()


class PublicDigitalRewardSerializer(serializers.Serializer):
    key = serializers.CharField()
    title = serializers.CharField()
    kind = serializers.CharField()
    config = serializers.DictField()
    unlocked_at = serializers.DateTimeField()
    selected = serializers.BooleanField()


class PublicProfileCommentSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    post_id = serializers.IntegerField()
    post_title = serializers.CharField()
    content = serializers.CharField()
    created_at = serializers.DateTimeField()


class PublicProfileStatsSerializer(serializers.Serializer):
    comments = serializers.IntegerField()
    approved_memes = serializers.IntegerField()
    approved_songs = serializers.IntegerField()
    poll_answers = serializers.IntegerField()
    badges = serializers.IntegerField()
    rewards = serializers.IntegerField()


class PublicProfileResponseSerializer(serializers.Serializer):
    user = PublicUserSummarySerializer()
    is_owner = serializers.BooleanField()
    profile_is_public = serializers.BooleanField()
    email_is_public = serializers.BooleanField()
    comments_are_public = serializers.BooleanField()
    bio = serializers.CharField(allow_blank=True)
    level = GamificationLevelSerializer(allow_null=True)
    points = serializers.IntegerField()
    badges = PublicEarnedBadgeSerializer(many=True)
    rewards = PublicDigitalRewardSerializer(many=True)
    stats = PublicProfileStatsSerializer()
    recent_comments = PublicProfileCommentSerializer(many=True)
    comments_visible = serializers.BooleanField()


class EventSerializer(serializers.ModelSerializer):
    class Meta:
        model = Event
        fields = '__all__'


class ChangelogSerializer(serializers.ModelSerializer):
    class Meta:
        model = Changelog
        fields = ['content', 'updated_at']


class TermsOfServiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = TermsOfService
        fields = ['content', 'date']

class PrivacyPolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = PrivacyPolicy
        fields = ['content', 'date']


class BellSongSuggestionSerializer(serializers.ModelSerializer):
    title = serializers.CharField(
        required=True,
        allow_blank=False,
        max_length=255,
        trim_whitespace=True,
    )
    note = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        max_length=1000,
        trim_whitespace=True,
    )
    user_username = serializers.SerializerMethodField()
    has_voted = serializers.SerializerMethodField()

    class Meta:
        model = BellSongSuggestion
        fields = ['id', 'link', 'slot', 'note', 'title', 'status', 'submitted_at', 'user_username', 'votes', 'has_voted']
        read_only_fields = ['user', 'status', 'submitted_at', 'votes', 'user_username', 'has_voted']

    def get_has_voted(self, obj):
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        if not user:
            return False
        if user.is_authenticated:
            annotated = getattr(obj, 'user_has_voted', None)
            if annotated is not None:
                return annotated
            return obj.voted_by.filter(id=user.id).exists()
        return False

    def get_user_username(self, obj):
        return obj.user.username if obj.user else None


    def validate_link(self, value):
        # YouTube regex
        youtube_regex = r'(?:https?://)?(?:www\.)?(?:m\.)?(?:youtube\.com|youtu\.be)/(?:watch\?v=|embed/|v/|)([\w-]{11})(?:\S+)?'
        # Spotify regex (track or episode)
        spotify_regex = r'(?:https?://)?(?:www\.)?(?:open\.spotify\.com)/(?:track|episode)/([a-zA-Z0-9]{22})(?:\S+)?'

        if re.match(youtube_regex, value) or re.match(spotify_regex, value):
            return value
        raise serializers.ValidationError("Невалиден YouTube или Spotify линк.")


class MemeOfWeekSerializer(serializers.ModelSerializer):
    title = serializers.CharField(
        required=True,
        allow_blank=False,
        allow_null=False,
        max_length=100,
        trim_whitespace=True,
    )
    image = serializers.ImageField(
        write_only=True,
        required=True,
        allow_null=False,
        validators=[validate_meme_image_size],
    )
    user_username = serializers.SerializerMethodField()
    image_url = serializers.SerializerMethodField()
    has_voted = serializers.SerializerMethodField()

    class Meta:
        model = MemeOfWeek
        fields = ['id', 'title', 'image', 'image_url', 'user_username', 'created_at', 'status', 'votes', 'has_voted']
        read_only_fields = ['id', 'user_username', 'created_at', 'image_url', 'status', 'votes', 'has_voted']

    def get_image_url(self, obj):
        if obj.pk and obj.image:
            return reverse('meme-image', kwargs={'pk': obj.pk})
        return None

    def get_has_voted(self, obj):
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        if not user:
            return False
        if user.is_authenticated:
            annotated = getattr(obj, 'user_has_voted', None)
            if annotated is not None:
                return annotated
            return obj.voted_by.filter(id=user.id).exists()
        return False

    def get_user_username(self, obj):
        return obj.user.username if obj.user else None

class ConsentRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConsentRecord
        fields = ['consent_status', 'policy_version']
        read_only_fields = ['id', 'timestamp', 'user', 'ip_address']

class SiteSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = SiteSettings
        fields = ['maintenance_mode', 'enable_bell_suggestions', 'enable_weekly_poll', 'enable_meme_of_the_week', 'enable_user_registration', 'enable_program_page']


class ScheduleEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = ScheduleEntry
        fields = ['class_name', 'lesson_number', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday']


class BlogLoginSessionSerializer(serializers.ModelSerializer):
    auth_method_display = serializers.CharField(
        source='get_auth_method_display',
        read_only=True,
    )
    revoked_reason_display = serializers.SerializerMethodField(
        help_text='Човеко-четимо описание на причината за прекратяване.',
    )
    status = serializers.SerializerMethodField(
        help_text='Текущ статус: active, expired или revoked.',
    )
    is_current = serializers.SerializerMethodField(
        help_text='Дали това е сесията на текущата API заявка.',
    )

    class Meta:
        model = BlogLoginSession
        fields = (
            'id',
            'auth_method',
            'auth_method_display',
            'ip_address',
            'network_operator',
            'network_asn',
            'device_label',
            'created_at',
            'last_seen_at',
            'expires_at',
            'revoked_at',
            'revoked_reason',
            'revoked_reason_display',
            'status',
            'is_current',
        )
        read_only_fields = fields

    def get_revoked_reason_display(self, obj) -> str:
        return obj.get_revoked_reason_display() if obj.revoked_reason else ''

    def get_status(self, obj) -> str:
        if obj.revoked_at is not None:
            return 'revoked'
        if obj.expires_at <= timezone.now():
            return 'expired'
        return 'active'

    def get_is_current(self, obj) -> bool:
        return str(obj.pk) == str(self.context.get('current_session_id') or '')


class ApiDetailResponseSerializer(serializers.Serializer):
    detail = serializers.CharField(
        help_text='Съобщение с резултата или причината за грешката.',
    )


class RegistrationResponseSerializer(ApiDetailResponseSerializer):
    username = serializers.CharField()
    email = serializers.EmailField()
    verification_required = serializers.BooleanField()
    email_sent = serializers.BooleanField()
    auto_verified = serializers.BooleanField()
    email_failure_reason = serializers.ChoiceField(
        choices=(
            'daily_quota_exceeded',
            'monthly_quota_exceeded',
            'rate_limit_exceeded',
            'service_unavailable',
            'delivery_failed',
        ),
        allow_null=True,
        required=False,
    )


class EmailVerificationResendRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()


class BlogSessionRevokeOthersResponseSerializer(ApiDetailResponseSerializer):
    revoked_count = serializers.IntegerField(
        min_value=0,
        help_text='Брой прекратени активни сесии.',
    )


class PersonalDataExportSchemaSerializer(serializers.Serializer):
    format_version = serializers.CharField()
    scope = serializers.CharField()
    generated_at = serializers.DateTimeField()
    account = serializers.JSONField(
        help_text='Профилни данни, роли и информация за свързан Google профил.',
    )
    created_content = serializers.JSONField(
        help_text='Публикации, коментари, предложения, мемета и контактни форми на потребителя.',
    )
    activity = serializers.JSONField(
        help_text='Отговори на анкети и направени гласове.',
    )
    privacy_and_security = serializers.JSONField(
        help_text='Записи за съгласие и история на сесиите в рамките на срока за съхранение.',
    )
    excluded_for_security = serializers.ListField(
        child=serializers.CharField(),
    )
    notes = serializers.ListField(child=serializers.CharField())



class BlogTokenObtainPairSerializer(TokenObtainPairSerializer):
    def validate(self, attrs):
        data = TokenObtainSerializer.validate(self, attrs)
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        if not profile.email_verified:
            raise serializers.ValidationError({
                'detail': (
                    'Потвърдете имейл адреса си, преди да влезете.'
                ),
                'code': 'email_not_verified',
            })
        _, refresh = issue_session_tokens(
            self.user,
            self.context['request'],
            auth_method='password',
        )
        data['refresh'] = str(refresh)
        data['access'] = str(refresh.access_token)
        data['user'] = CurrentUserSerializer(
            self.user,
            context=self.context,
        ).data
        if settings.SIMPLE_JWT.get('UPDATE_LAST_LOGIN', False):
            update_last_login(None, self.user)
        return data


class MyTokenObtainPairView(TokenObtainPairView):
    serializer_class = BlogTokenObtainPairSerializer
    throttle_scope = 'login'

    def post(self, request, *args, **kwargs):
        # 1. Взимаме токена от React (captcha_answer)
        captcha_token = request.data.get('captcha_answer')
        
        secret_key = getattr(settings, 'TURNSTILE_SECRET_KEY', '').strip()
        verify_url = getattr(
            settings,
            'TURNSTILE_VERIFY_URL',
            'https://challenges.cloudflare.com/turnstile/v0/siteverify',
        )

        if not secret_key:
            return Response(
                {"detail": "Входът временно не е достъпен."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        if not captcha_token:
            return Response(
                {"detail": "Моля, потвърдете, че не сте робот."}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        # 2. Проверка към Cloudflare
        try:
            payload = {
                'secret': secret_key,
                'response': captcha_token,
            }
            remote_ip = get_client_ip(request)
            if remote_ip:
                payload['remoteip'] = remote_ip
            verify_res = requests.post(
                verify_url,
                data=payload,
                timeout=5
            )
            verify_res.raise_for_status()
            if not verify_res.json().get('success'):
                return Response(
                    {"detail": "Защитата засича автоматизирана дейност (Бот)."}, 
                    status=status.HTTP_403_FORBIDDEN
                )
        except (requests.exceptions.RequestException, ValueError):
            return Response(
                {"detail": "Грешка при връзка със защитната система."}, 
                status=status.HTTP_503_SERVICE_UNAVAILABLE
            )

        # 3. Ако всичко е наред, викаме оригиналния логин (проверка на име и парола)
        return super().post(request, *args, **kwargs)
