from rest_framework import serializers
from .models import Posts, UserProfile, Comments, PollQuestion, PollAnswer, PollOption, ContactSubmission, Notification, \
    Event, TermsOfService, PostImage, BellSongSuggestion, PrivacyPolicy, MemeOfWeek, Cookie, SiteSettings, PostDocument, Changelog
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.conf import settings
import requests
from bs4 import BeautifulSoup
import re
import markdown # New import
from django.utils.safestring import mark_safe # New import
import requests
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework.response import Response
from rest_framework import status

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
        return obj.author.username

    def get_images(self, obj):
        images = obj.images.all()
        return [image.image.url for image in images if image.image]

    def get_documents(self, obj):
        request = self.context.get('request')
        documents = obj.documents.all()
        return [PostDocumentSerializer(doc, context={'request': request}).data for doc in documents]

class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(
        write_only=True,
        required=True,
        validators=[validate_password]
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
        extra_kwargs = {
            'first_name': {'required': True},
            'last_name': {'required': True},
            'email': {'required': True},
        }

    def validate(self, attrs):
        # 1. Проверка на паролите
        if attrs['password'] != attrs['password2']:
            raise serializers.ValidationError({"password": "Паролите не съвпадат."})

        # 2. Вземаме токена и Secret Key
        captcha_token = attrs.get('captcha_answer')
        secret_key = settings.TURNSTILE_SECRET_KEY
        if not secret_key:
            raise serializers.ValidationError(
                {"captcha": "Защитата не е конфигурирана на сървъра."}
            )

        # 3. Валидация през API-то на Cloudflare
        try:
            verify_response = requests.post(
                "https://challenges.cloudflare.com/turnstile/v0/siteverify",
                data={
                    'secret': secret_key,
                    'response': captcha_token,
                },
                timeout=5 # Добра практика е да има таймаут
            )
            
            result = verify_response.json()
            
            if not result.get('success'):
                raise serializers.ValidationError({"captcha": "Неуспешна проверка за ботове. Опитайте пак."})
                
        except requests.exceptions.RequestException:
            raise serializers.ValidationError({"captcha": "Грешка при връзка със защитната система."})

        # Премахваме помощните полета
        attrs.pop('password2')
        attrs.pop('captcha_answer')
        return attrs

    def create(self, validated_data):
        # Създаваме потребителя
        user = User.objects.create(
            username=validated_data['username'],
            email=validated_data['email'],
            first_name=validated_data['first_name'],
            last_name=validated_data['last_name']
        )
        user.set_password(validated_data['password'])
        user.save()

        # Създаваме свързания UserProfile
        UserProfile.objects.get_or_create(user=user)

        return user


class PasswordChangeSerializer(serializers.Serializer):
    current_password = serializers.CharField(style={"input_type": "password"}, required=True)
    new_password = serializers.CharField(style={"input_type": "password"}, required=True)
    new_password_confirm = serializers.CharField(style={"input_type": "password"}, required=True)

    def validate_new_password(self, value):
        validate_password(value)
        return value

    def validate(self, data):
        if data['new_password'] != data['new_password_confirm']:
            raise serializers.ValidationError({"new_password": "Новата парола и потвърждението не съвпадат."})
        return data


class UsernameChangeSerializer(serializers.Serializer):
    current_password = serializers.CharField(style={"input_type": "password"}, required=True)
    new_username = serializers.CharField(required=True)

    def validate_new_username(self, value):
        user = self.context['request'].user
        if User.objects.exclude(pk=user.pk).filter(username__iexact=value).exists():
            raise serializers.ValidationError("Това потребителско име вече е заето.")
        return value





class CommentSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)
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
            return obj.parent.user.username
        return None

    def get_reply_count(self, obj):
        if obj.parent is None:
            return obj.replies.count()
        return 0

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
    id = serializers.IntegerField(read_only=False)
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


class UserPollStatusSerializer(serializers.Serializer):
    is_locked = serializers.BooleanField()
    unlocks_at = serializers.DateTimeField(allow_null=True)
    last_result = serializers.JSONField(allow_null=True)
    question = PollQuestionSerializer(allow_null=True)


class LeaderboardEntrySerializer(serializers.ModelSerializer):
    correct_answers = serializers.IntegerField()

    class Meta:
        model = User
        fields = ['id', 'username', 'correct_answers']


class RecentParticipantSerializer(serializers.ModelSerializer):
    last_answered = serializers.DateTimeField()

    class Meta:
        model = User
        fields = ['id', 'username', 'last_answered']


class PollStatisticsSerializer(serializers.Serializer):
    leaderboard = LeaderboardEntrySerializer(many=True)
    recent_participants = RecentParticipantSerializer(many=True)


class ContactSubmissionSerializer(serializers.ModelSerializer):
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
        return mark_safe(markdown.markdown(obj.text, extensions=['nl2br']))


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
    title = serializers.CharField(required=True)
    user_username = serializers.CharField(source='user.username', read_only=True)
    has_voted = serializers.SerializerMethodField()

    class Meta:
        model = BellSongSuggestion
        fields = ['id', 'link', 'slot', 'note', 'title', 'status', 'submitted_at', 'user_username', 'votes', 'has_voted']
        read_only_fields = ['user', 'status', 'submitted_at', 'votes', 'user_username', 'has_voted']

    def get_has_voted(self, obj):
        user = self.context['request'].user
        if user.is_authenticated:
            return obj.voted_by.filter(id=user.id).exists()
        return False


    def validate_link(self, value):
        # YouTube regex
        youtube_regex = r'(?:https?://)?(?:www\.)?(?:m\.)?(?:youtube\.com|youtu\.be)/(?:watch\?v=|embed/|v/|)([\w-]{11})(?:\S+)?'
        # Spotify regex (track or episode)
        spotify_regex = r'(?:https?://)?(?:www\.)?(?:open\.spotify\.com)/(?:track|episode)/([a-zA-Z0-9]{22})(?:\S+)?'

        if re.match(youtube_regex, value) or re.match(spotify_regex, value):
            return value
        raise serializers.ValidationError("Невалиден YouTube или Spotify линк.")


class MemeOfWeekSerializer(serializers.ModelSerializer):
    user_username = serializers.CharField(source='user.username', read_only=True)
    image_url = serializers.SerializerMethodField()
    has_voted = serializers.SerializerMethodField()

    class Meta:
        model = MemeOfWeek
        fields = ['id', 'title', 'image', 'image_url', 'user_username', 'created_at', 'is_approved', 'votes', 'has_voted']
        read_only_fields = ['id', 'user_username', 'created_at', 'image_url', 'is_approved', 'votes', 'has_voted']
        # 'image' is write-only, the URL is for reading
        extra_kwargs = {
            'image': {'write_only': True, 'required': True},
            'title': {'required': True}
        }

    def get_image_url(self, obj):
        if obj.image and hasattr(obj.image, 'url'):
            return obj.image.url
        return None

    def get_has_voted(self, obj):
        user = self.context['request'].user
        if user.is_authenticated:
            return obj.voted_by.filter(id=user.id).exists()
        return False

class ConsentRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = Cookie.ConsentRecord
        fields = ['consent_status', 'policy_version']
        read_only_fields = ['id', 'timestamp', 'user', 'ip_address']

class SiteSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = SiteSettings
        fields = ['maintenance_mode', 'enable_bell_suggestions', 'enable_weekly_poll', 'enable_meme_of_the_week', 'enable_user_registration', 'enable_program_page']



class MyTokenObtainPairView(TokenObtainPairView):
    def post(self, request, *args, **kwargs):
        # 1. Взимаме токена от React (captcha_answer)
        captcha_token = request.data.get('captcha_answer')
        
        # Твоят таен ключ (същият като в RegisterSerializer)
        secret_key = settings.TURNSTILE_SECRET_KEY
        if not secret_key:
            return Response(
                {"detail": "Защитата не е конфигурирана на сървъра."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        if not captcha_token:
            return Response(
                {"detail": "Моля, потвърдете, че не сте робот."}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        # 2. Проверка към Cloudflare
        try:
            verify_res = requests.post(
                "https://challenges.cloudflare.com/turnstile/v0/siteverify",
                data={
                    'secret': secret_key,
                    'response': captcha_token,
                },
                timeout=5
            )
            
            if not verify_res.json().get('success'):
                return Response(
                    {"detail": "Защитата засича автоматизирана дейност (Бот)."}, 
                    status=status.HTTP_403_FORBIDDEN
                )
        except requests.exceptions.RequestException:
            return Response(
                {"detail": "Грешка при връзка със защитната система."}, 
                status=status.HTTP_503_SERVICE_UNAVAILABLE
            )

        # 3. Ако всичко е наред, викаме оригиналния логин (проверка на име и парола)
        return super().post(request, *args, **kwargs)
