from rest_framework import serializers
from .models import Posts, UserProfile, Comments, PollQuestion, PollAnswer, PollOption, ContactSubmission, Notification, \
    Event, TermsOfService, PostImage, BellSongSuggestion, PrivacyPolicy, MemeOfWeek, ConsentRecord, SiteSettings, PostDocument, Changelog, ScheduleEntry, \
    validate_meme_image_size
from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse
import requests
import re
from django.utils.html import escape
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework.response import Response
from rest_framework import status
from .network import get_client_ip

class PostImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = PostImage
        fields = ['image']

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
    category_name = serializers.StringRelatedField(source='category')
    images = serializers.SerializerMethodField()
    documents = serializers.SerializerMethodField()
    banner = serializers.SerializerMethodField()

    class Meta:
        model = Posts
        fields = (
            'id',
            'category',       # Запазваме ID за бъдещи филтрирания
            'category_name',  # ⬅️ Добавено: Име на категория
            'author',         # Запазваме ID на автора
            'author_username',# ⬅️ Добавено: Потребителско име
            'title',
            'banner',
            'hook',
            'content',
            'created_at',
            'published_at',
            'published',
            'allowed',
            'images',
            'documents' # Add documents here
        )

    def get_banner(self, obj):
        if obj.banner and hasattr(obj.banner, 'url'):
            return obj.banner.url
        return None

    def get_author_username(self, obj):
        return obj.author.username if obj.author else None

    def get_images(self, obj):
        images = obj.images.all()
        return [image.image.url for image in images if image.image]

    def get_documents(self, obj):
        request = self.context.get('request')
        documents = obj.documents.all()
        return [PostDocumentSerializer(doc, context={'request': request}).data for doc in documents]

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
                UserProfile.objects.get_or_create(user=user)
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
    post_id = serializers.IntegerField(source='post.id', read_only=True)
    parent_id = serializers.PrimaryKeyRelatedField(source='parent', read_only=True)
    parent_username = serializers.SerializerMethodField()
    reply_count = serializers.SerializerMethodField()

    class Meta:
        model = Comments
        fields = ['id', 'post_id', 'username', 'content', 'created_at', 'parent', 'parent_id', 'parent_username', 'reply_count']
        extra_kwargs = {
            'parent': {'write_only': True, 'required': False, 'allow_null': True, 'queryset': Comments.objects.all()},
        }
        read_only_fields = ['id', 'created_at', 'username', 'post_id', 'parent_id', 'parent_username', 'reply_count']

    def get_parent_username(self, obj):
        if obj.parent:
            return obj.parent.user.username if obj.parent.user else None
        return None

    def get_username(self, obj):
        return obj.user.username if obj.user else None

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
    class Meta:
        model = Notification
        fields = ['id', 'text', 'enabled', 'created_at', 'html_text']
        read_only_fields = ['html_text']

    def get_html_text(self, obj):
        # Notifications are rendered as escaped plaintext. Only line breaks are
        # converted, so Markdown cannot synthesize javascript: links.
        return '<br>'.join(escape(obj.text).splitlines())


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
        fields = ['id', 'title', 'image', 'image_url', 'user_username', 'created_at', 'is_approved', 'votes', 'has_voted']
        read_only_fields = ['id', 'user_username', 'created_at', 'image_url', 'is_approved', 'votes', 'has_voted']

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



class MyTokenObtainPairView(TokenObtainPairView):
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
