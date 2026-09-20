import hashlib
import secrets
import uuid
from datetime import timedelta
from django.conf import settings
from django.contrib.auth.models import Group, User
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models, transaction
from django.db.models import BooleanField
from django.db.models.signals import m2m_changed, post_delete, post_save, pre_delete
from django.dispatch import receiver
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.core.validators import (
    FileExtensionValidator,
    MaxValueValidator,
    MinValueValidator,
)
from .avatars import (
    AVATAR_PRESETS,
    avatar_preset_url,
    profile_avatar_upload_path,
    validate_avatar_upload_size,
)
from .team_chat_storage import team_chat_protected_storage


def team_chat_attachment_upload_path(instance, filename):
    return f'{instance.pk}/image.webp'


def team_chat_attachment_thumbnail_upload_path(instance, filename):
    return f'{instance.pk}/thumbnail.webp'


def calculate_file_sha256(file_obj):
    """Return a content hash without changing the caller's file position."""

    digest = hashlib.sha256()
    was_closed = bool(getattr(file_obj, 'closed', False))
    try:
        original_position = file_obj.tell()
    except (AttributeError, OSError, ValueError):
        original_position = None

    try:
        if hasattr(file_obj, 'seek'):
            file_obj.seek(0)
        if hasattr(file_obj, 'chunks'):
            chunks = file_obj.chunks()
        else:
            chunks = iter(lambda: file_obj.read(1024 * 1024), b'')
        for chunk in chunks:
            digest.update(chunk)
    finally:
        if was_closed and hasattr(file_obj, 'close'):
            file_obj.close()
        elif original_position is not None and hasattr(file_obj, 'seek'):
            try:
                file_obj.seek(original_position)
            except (OSError, ValueError):
                pass

    return digest.hexdigest()

def post_media_upload_path(instance, filename, field_type):
    """
    Build a predictable path for media that belongs to a saved post.

    The admin saves a new post before committing its uploads, so every file is
    stored below ``posts/<post id>/``.  Failing explicitly here prevents a
    future code path from silently bringing back permanent UUID directories.
    """
    post_id = None
    if hasattr(instance, 'post') and instance.post.pk:
        # Instance is a related model (PostImage, PostDocument) with a saved parent post.
        post_id = instance.post.pk
    elif hasattr(instance, 'pk') and instance.pk:
        # Instance is the main Post model and it has been saved.
        post_id = instance.pk

    if post_id is None:
        raise ValueError(
            'The post must be saved before its media files are uploaded.'
        )

    return f'posts/{post_id}/{field_type}/{filename}'


def post_banner_upload_path(instance, filename):
    return post_media_upload_path(instance, filename, 'banner')

def post_gallery_upload_path(instance, filename):
    return post_media_upload_path(instance, filename, 'gallery')

def post_document_upload_path(instance, filename):
    return post_media_upload_path(instance, filename, 'documents')

def meme_upload_path(instance, filename):
    owner = f'user-{instance.user_id}' if instance.user_id else 'anonymous'
    return f'memes/{owner}/{filename}'


def validate_meme_image_size(image):
    """Keep public meme uploads within a reasonable, configurable size."""
    max_size = getattr(settings, 'MAX_MEME_UPLOAD_SIZE', 5 * 1024 * 1024)
    if image and image.size > max_size:
        max_size_mb = max_size / (1024 * 1024)
        raise ValidationError(
            f"Изображението не може да е по-голямо от {max_size_mb:g} MB."
        )


DOCUMENT_EXTENSIONS = (
    'pdf',
    'docx',
    'doc',
    'xlsx',
    'xls',
    'pptx',
    'ppt',
    'zip',
    'rar',
    '7z',
    'csv',
    'txt',
    'json',
    'rtf',
)


def validate_image_upload_size(image):
    max_size = getattr(settings, 'MAX_IMAGE_UPLOAD_SIZE', 10 * 1024 * 1024)
    if image and image.size > max_size:
        max_size_mb = max_size / (1024 * 1024)
        raise ValidationError(
            f"Изображението не може да е по-голямо от {max_size_mb:g} MB."
        )


def validate_document_upload_size(document):
    max_size = getattr(
        settings,
        'MAX_DOCUMENT_UPLOAD_SIZE',
        25 * 1024 * 1024,
    )
    if document and document.size > max_size:
        max_size_mb = max_size / (1024 * 1024)
        raise ValidationError(
            f"Документът не може да е по-голям от {max_size_mb:g} MB."
        )


def default_poll_end_date():
    return timezone.now() + timedelta(days=7)


def poll_media_upload_path(instance, filename):
    """
    Generates an upload path for poll-related media.
    """
    # Check if we're dealing with a PollQuestion or a PollOption
    if isinstance(instance, PollQuestion):
        poll_id = instance.pk if instance.pk else uuid.uuid4().hex
        return f'polls/{poll_id}/question/{filename}'
    elif isinstance(instance, PollOption):
        poll_id = instance.question.pk if instance.question.pk else uuid.uuid4().hex
        return f'polls/{poll_id}/options/{filename}'
    # Fallback path
    return f'polls/unknown/{filename}'

class Category(models.Model):
    full_name = models.CharField(max_length=100, help_text="Пълното име на категорията, което ще се показва на сайта.")
    short_name = models.SlugField(max_length=100, unique=True, help_text="Кратко име в URL съвместим формат (слаг). Например: 'novini-ot-uchilishte'.")
    def __str__(self):
        return self.full_name

    class Meta:
        verbose_name = "Категория"
        verbose_name_plural = "Категории"

class Posts(models.Model):
    title = models.CharField(max_length=100, blank=False, help_text="Заглавието на публикацията до 100 символа.")
    categories = models.ManyToManyField(
        Category,
        related_name='posts',
        blank=True,
        verbose_name="Категории",
        help_text="Категориите, към които принадлежи публикацията. 'Общи' се добавя автоматично. Максимум 3.",
    )
    banner = models.ImageField(upload_to=post_banner_upload_path, blank=True, null=True, validators=[validate_image_upload_size], help_text="Банер изображение, което ще се показва в горната част на публикацията.")
    hook = models.TextField(max_length=100, help_text="Кратко въведение или 'кукичка', което да привлече вниманието на читателя до 100 символа.")
    content = models.TextField(blank=False, help_text="Пълното съдържание на публикацията. Поддържа се Markdown формат.")
    author = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Авторът на публикацията. Полето се попълва автоматично.",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Създадена на",
        help_text="Записва се автоматично при първото създаване на публикацията.",
    )
    published_at = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name="Публикувай на",
        help_text="Оставете празно за незабавно публикуване. Бъдеща дата пази статията скрита до зададения момент.",
    )
    published = BooleanField(
        default=False,
        help_text="Включете, за да бъде публикацията публикувана веднага или на зададената дата.",
    )
    allowed = models.BooleanField(default=False, help_text="Отбележете, за да одобрите публикацията за показване (за публикации, изискващи одобрение).")
    publication_notified_at = models.DateTimeField(
        null=True,
        blank=True,
        editable=False,
        verbose_name="Известие за публикуване изпратено на",
    )

    class Meta:
        permissions = [
            ('view_only_posts', 'Може да вижда собствените си публикации'),
            ('can_view_all_posts', 'Може да вижда всички публикувани публикации'),
            ('can_view_all_pnp_posts', 'Може да вижда всички публикации, включително чернови'),
            ('can_allow_posts', 'Може да одобрява публикации'),
            ('can_edit_users_post', 'Може да редактира публикации на други потребители'),
        ]
        verbose_name = "Публикация"
        verbose_name_plural = "Публикации"

    def __str__(self):
        return self.title

    def clean(self):
        super().clean()
        if self.pk and self.categories.count() > 3:
            raise ValidationError(
                {'categories': 'Могат да бъдат избрани най-много 3 категории.'}
            )

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

class Comments(models.Model):
    content = models.TextField(blank=False, help_text="Съдържанието на коментара.")
    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Потребителят, който е написал коментара.",
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="Дата и час на създаване на коментара. Формат: YYYY-MM-DD HH:MM:SS.")
    post = models.ForeignKey(Posts, on_delete=models.CASCADE, help_text="Публикацията, към която е коментарът.")
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='replies', help_text="Родителският коментар, ако този е отговор.")

    class Meta:
        ordering = ['created_at']

    def clean(self):
        super().clean()
        if (
            self.parent_id
            and self.post_id
            and self.parent.post_id != self.post_id
        ):
            raise ValidationError({
                'parent': (
                    'Родителският коментар не принадлежи '
                    'към тази публикация.'
                )
            })

    def save(self, *args, **kwargs):
        raw = kwargs.pop('raw', False)
        if not raw:
            self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.content


SLOT_CHOICES = (
    ('startClass', 'Начало на час'),
    ('endClass', 'Край на час'),
    ('beforeLunch', 'Преди голямо междучасие'),
    ('afterLunch', 'След голямо междучасие'),
    ('morning', 'Сутрешен звънец'), # Default
    ('special', 'Специален повод'),
)

STATUS_CHOICES = (
    ('pending', 'Чакащо одобрение'),
    ('approved', 'Одобрено'),
    ('rejected', 'Отхвърлено'),
)

class BellSongSuggestion(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Потребител",
        help_text="Потребителят, който е направил предложението.",
    )
    title = models.CharField(max_length=255, verbose_name="Заглавие на песента", help_text="Името на песента.")
    link = models.URLField(max_length=2048, verbose_name="Линк към песента", help_text="URL към песента в Spotify или YouTube.")
    slot = models.CharField(max_length=20, choices=SLOT_CHOICES, default='morning', verbose_name="Кога да звучи", help_text="Изберете кога да звучи песента.")
    note = models.TextField(blank=True, null=True, verbose_name="Бележка", help_text="Допълнителна информация или коментар към предложението.")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending', verbose_name="Статус", help_text="Статус на предложението (чакащо, одобрено, отхвърлено).")
    submitted_at = models.DateTimeField(auto_now_add=True, verbose_name="Изпратено на", help_text="Дата и час на изпращане на предложението. Формат: YYYY-MM-DD HH:MM:SS.")
    voted_by = models.ManyToManyField(User, related_name='voted_songs', blank=True, verbose_name="Гласували потребители", help_text="Потребители, които са гласували за тази песен.")
    votes = models.IntegerField(default=0, verbose_name="Гласове", help_text="Брой на гласовете за песента.")

    class Meta:
        verbose_name = "Предложение за песен за звънец"
        verbose_name_plural = "Предложения за песни за звънец"
        ordering = ['-submitted_at']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(votes__gte=0),
                name='blog_song_votes_nonnegative',
            ),
        ]

    def __str__(self):
        return f"{self.title} ({self.get_status_display()})"

class MemeOfWeek(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Потребител",
        help_text="Потребителят, който е качил мемето.",
    )
    title = models.CharField(max_length=100, blank=False, null=True, verbose_name="Заглавие", help_text="Заглавие на мемето.")
    image = models.ImageField(
        upload_to=meme_upload_path,
        blank=False,
        null=True,
        validators=[validate_meme_image_size],
        verbose_name="Изображение",
        help_text="Файлът с изображението на мемето.",
    )
    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default='pending',
        verbose_name="Статус",
        help_text="Статус на мемето: изчакващо, одобрено или отхвърлено.",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата на качване", null=True, help_text="Дата и час на качване на мемето. Формат: YYYY-MM-DD HH:MM:SS.")
    voted_by = models.ManyToManyField(User, related_name='voted_memes', blank=True, verbose_name="Гласували потребители", help_text="Потребители, които са гласували за това меме.")
    votes = models.IntegerField(default=0, verbose_name="Гласове", help_text="Брой на гласовете за мемето.")

    class Meta:
        verbose_name = "Меме на седмицата"
        verbose_name_plural = "Мемета на седмицата"
        ordering = ['-created_at']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(votes__gte=0),
                name='blog_meme_votes_nonnegative',
            ),
        ]

    def __str__(self):
        if self.title:
            return f"{self.title} ({self.get_status_display()})"
        username = self.user.username if self.user else "изтрит потребител"
        return f"Meme by {username}"

class UserProfile(models.Model):
    class AvatarSource(models.TextChoices):
        INITIALS = 'initials', 'Инициали'
        UPLOAD = 'upload', 'Качена снимка'
        PRESET = 'preset', 'Готов аватар'
        GOOGLE = 'google', 'Google снимка'

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        help_text="Свързаният потребителски акаунт.",
    )
    avatar = models.ImageField(
        upload_to=profile_avatar_upload_path,
        blank=True,
        null=True,
        validators=[validate_avatar_upload_size],
        verbose_name='Профилна снимка',
        help_text='Локално съхранена и оптимизирана WebP профилна снимка.',
    )
    avatar_source = models.CharField(
        max_length=10,
        choices=AvatarSource.choices,
        default=AvatarSource.INITIALS,
        verbose_name='Източник на аватара',
    )
    avatar_preset = models.CharField(
        max_length=24,
        choices=AVATAR_PRESETS,
        blank=True,
        default='',
        verbose_name='Готов аватар',
    )
    avatar_user_selected = models.BooleanField(
        default=False,
        verbose_name='Направен изричен избор',
        help_text=(
            'Предотвратява автоматичен Google импорт след избор от потребителя '
            'или нулиране от администратор.'
        ),
    )
    avatar_updated_at = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name='Аватарът е променен на',
    )
    email_verified = models.BooleanField(
        default=True,
        verbose_name='Потвърден имейл',
        help_text=(
            'Само публичните регистрации с имейл и парола започват като '
            'непотвърдени. Google и администраторските акаунти са потвърдени.'
        ),
    )
    email_verified_at = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name='Имейлът е потвърден на',
    )
    gamification_points = models.PositiveIntegerField(
        default=0,
        db_index=True,
        verbose_name='Точки',
        help_text='Кеширан баланс, поддържан от gamification service.',
    )
    leaderboard_opt_in = models.BooleanField(
        default=False,
        verbose_name='Участва в класацията',
        help_text='Профилът се показва публично само след изрично съгласие.',
    )
    public_profile_enabled = models.BooleanField(
        default=True,
        verbose_name='Публичен профил',
        help_text='Позволява на останалите да отварят профилната страница.',
    )
    show_email_on_profile = models.BooleanField(
        default=False,
        verbose_name='Публичен имейл',
        help_text='Имейлът никога не е публичен без изрично включване.',
    )
    show_comments_on_profile = models.BooleanField(
        default=True,
        verbose_name='Публични коментари',
        help_text='Показва последните коментари към публични публикации.',
    )
    bio = models.CharField(
        max_length=500,
        blank=True,
        default='',
        verbose_name='За мен',
    )

    class Meta:
        verbose_name = 'Профилна снимка'
        verbose_name_plural = 'Профилни снимки'

    @property
    def avatar_url(self):
        if self.avatar_source == self.AvatarSource.PRESET:
            return avatar_preset_url(self.avatar_preset)
        if (
            self.avatar_source in {self.AvatarSource.UPLOAD, self.AvatarSource.GOOGLE}
            and self.avatar
        ):
            return self.avatar.url
        return None

    def __str__(self):
        return f"Профил на {self.user.username}"


class GamificationLevel(models.Model):
    number = models.PositiveSmallIntegerField(unique=True, verbose_name='Ниво')
    title = models.CharField(max_length=80, verbose_name='Име')
    icon = models.CharField(
        max_length=40,
        default='star',
        verbose_name='Икона',
        help_text='Име на икона от lucide-react.',
    )
    minimum_points = models.PositiveIntegerField(
        unique=True,
        verbose_name='Минимални точки',
    )

    class Meta:
        verbose_name = 'Ниво'
        verbose_name_plural = 'Нива'
        ordering = ('minimum_points',)

    def __str__(self):
        return f'Ниво {self.number} — {self.title}'


class Badge(models.Model):
    class Rarity(models.TextChoices):
        COMMON = 'common', 'Обикновена'
        RARE = 'rare', 'Рядка'
        EPIC = 'epic', 'Епична'
        SEASONAL = 'seasonal', 'Сезонна'

    slug = models.SlugField(max_length=80, unique=True)
    title = models.CharField(max_length=100, verbose_name='Име')
    description = models.TextField(verbose_name='Описание')
    icon = models.CharField(
        max_length=40,
        default='award',
        verbose_name='Икона',
        help_text='Име на икона от lucide-react.',
    )
    rarity = models.CharField(
        max_length=12,
        choices=Rarity.choices,
        default=Rarity.COMMON,
        verbose_name='Рядкост',
    )
    requirement = models.JSONField(
        default=dict,
        blank=True,
        verbose_name='Условие',
        help_text='Структурирано условие за автоматично отключване.',
    )
    active = models.BooleanField(default=True, verbose_name='Активна')
    starts_at = models.DateTimeField(null=True, blank=True, verbose_name='Начало')
    ends_at = models.DateTimeField(null=True, blank=True, verbose_name='Край')
    hidden = models.BooleanField(default=False, verbose_name='Тайна')
    points_reward = models.PositiveIntegerField(
        default=0,
        verbose_name='Бонус точки',
    )
    repeatable = models.BooleanField(default=False, verbose_name='Повторяема')
    digital_rewards = models.ManyToManyField(
        'DigitalReward',
        blank=True,
        related_name='badges',
        verbose_name='Дигитални награди',
    )

    class Meta:
        verbose_name = 'Значка'
        verbose_name_plural = 'Значки'
        ordering = ('title',)

    def __str__(self):
        return self.title


class UserBadge(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='earned_badges',
        verbose_name='Потребител',
    )
    badge = models.ForeignKey(
        Badge,
        on_delete=models.CASCADE,
        related_name='awards',
        verbose_name='Значка',
    )
    award_key = models.CharField(
        max_length=120,
        default='default',
        verbose_name='Ключ на наградата',
    )
    awarded_at = models.DateTimeField(auto_now_add=True, verbose_name='Получена на')
    awarded_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='manually_awarded_badges',
        verbose_name='Дадена от',
    )
    reason = models.CharField(max_length=255, blank=True, default='')

    class Meta:
        verbose_name = 'Получена значка'
        verbose_name_plural = 'Получени значки'
        ordering = ('-awarded_at',)
        constraints = [
            models.UniqueConstraint(
                fields=('user', 'badge', 'award_key'),
                name='blog_unique_badge_award_key',
            ),
        ]

    def __str__(self):
        return f'{self.user} — {self.badge}'


class PointTransaction(models.Model):
    class Source(models.TextChoices):
        ACTIVITY = 'activity', 'Активност'
        BADGE = 'badge', 'Значка'
        ADMIN = 'admin', 'Администратор'
        REVERSAL = 'reversal', 'Корекция'

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='point_transactions',
        verbose_name='Потребител',
    )
    action = models.CharField(max_length=60, db_index=True, verbose_name='Действие')
    points = models.SmallIntegerField(verbose_name='Промяна')
    balance_after = models.PositiveIntegerField(verbose_name='Баланс след промяната')
    description = models.CharField(max_length=255, verbose_name='Описание')
    source = models.CharField(
        max_length=12,
        choices=Source.choices,
        default=Source.ACTIVITY,
        verbose_name='Източник',
    )
    reference_type = models.CharField(max_length=60, blank=True, default='')
    reference_id = models.CharField(max_length=80, blank=True, default='')
    idempotency_key = models.CharField(
        max_length=190,
        unique=True,
        null=True,
        blank=True,
        verbose_name='Idempotency ключ',
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_point_transactions',
        verbose_name='Извършено от',
    )
    reversal_of = models.OneToOneField(
        'self',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='reversal',
        verbose_name='Корекция на',
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Създадена на')

    class Meta:
        verbose_name = 'Транзакция с точки'
        verbose_name_plural = 'История на точките'
        ordering = ('-created_at', '-pk')
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(points=0),
                name='blog_point_transaction_nonzero',
            ),
        ]
        indexes = [
            models.Index(fields=('user', 'created_at'), name='points_user_created_idx'),
            models.Index(
                fields=('reference_type', 'reference_id'),
                name='points_reference_idx',
            ),
        ]

    def __str__(self):
        sign = '+' if self.points > 0 else ''
        return f'{self.user}: {sign}{self.points} — {self.description}'


class GamificationPointRule(models.Model):
    ACTION_CHOICES = (
        ('poll_vote', 'Гласуване в анкета'),
        ('comment', 'Валиден коментар'),
        ('song_approved', 'Одобрено предложение за песен'),
        ('meme_approved', 'Одобрено меме'),
        ('post_read', 'Прочетена публикация'),
        ('calendar_visit', 'Посещение на календара'),
    )

    action = models.CharField(
        max_length=40,
        unique=True,
        choices=ACTION_CHOICES,
        verbose_name='Действие',
    )
    points = models.PositiveSmallIntegerField(default=1, verbose_name='Точки')
    daily_limit = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        verbose_name='Дневен лимит',
        help_text='Оставете празно за неограничен брой наградени действия на ден.',
    )
    active = models.BooleanField(default=True, verbose_name='Активно')

    class Meta:
        verbose_name = 'Правило за точки'
        verbose_name_plural = 'Правила за точки'
        ordering = ('action',)

    def __str__(self):
        return f'{self.get_action_display()}: {self.points} т.'


class Mission(models.Model):
    slug = models.SlugField(max_length=100, unique=True)
    title = models.CharField(max_length=120, verbose_name='Име')
    description = models.TextField(verbose_name='Описание')
    start_date = models.DateTimeField(verbose_name='Начало')
    end_date = models.DateTimeField(verbose_name='Край')
    reward_points = models.PositiveIntegerField(default=0, verbose_name='Награда')
    active = models.BooleanField(default=True, verbose_name='Активна')
    requirements = models.JSONField(
        default=list,
        verbose_name='Изисквания',
        help_text='Списък с key, action, label и target за всяка цел.',
    )
    availability_notified_at = models.DateTimeField(
        null=True,
        blank=True,
        editable=False,
        verbose_name='Известие за мисията изпратено на',
    )

    class Meta:
        verbose_name = 'Мисия'
        verbose_name_plural = 'Мисии'
        ordering = ('-start_date',)
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_date__gt=models.F('start_date')),
                name='blog_mission_end_after_start',
            ),
        ]

    def __str__(self):
        return self.title


class UserMissionProgress(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='mission_progress',
        verbose_name='Потребител',
    )
    mission = models.ForeignKey(
        Mission,
        on_delete=models.CASCADE,
        related_name='user_progress',
        verbose_name='Мисия',
    )
    progress = models.JSONField(default=dict, blank=True, verbose_name='Прогрес')
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name='Завършена на')
    rewarded_at = models.DateTimeField(null=True, blank=True, verbose_name='Наградена на')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Обновена на')

    class Meta:
        verbose_name = 'Прогрес по мисия'
        verbose_name_plural = 'Прогрес по мисии'
        constraints = [
            models.UniqueConstraint(
                fields=('user', 'mission'),
                name='blog_unique_user_mission_progress',
            ),
        ]

    def __str__(self):
        return f'{self.user} — {self.mission}'


class MissionActivity(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='mission_activities',
    )
    mission = models.ForeignKey(
        Mission,
        on_delete=models.CASCADE,
        related_name='activities',
    )
    action = models.CharField(max_length=50, db_index=True)
    reference_id = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Активност по мисия'
        verbose_name_plural = 'Активности по мисии'
        constraints = [
            models.UniqueConstraint(
                fields=('user', 'mission', 'action', 'reference_id'),
                name='blog_unique_mission_activity',
            ),
        ]
        indexes = [
            models.Index(
                fields=('user', 'mission', 'action'),
                name='mission_activity_lookup_idx',
            ),
        ]


class DigitalReward(models.Model):
    class Kind(models.TextChoices):
        AVATAR_FRAME = 'avatar_frame', 'Рамка за аватар'
        PROFILE_BORDER = 'profile_border', 'Рамка за профил'
        PROFILE_THEME = 'profile_theme', 'Тема за профил'
        USER_TITLE = 'user_title', 'Титла'
        SPECIAL_ICON = 'special_icon', 'Специална икона'

    key = models.SlugField(max_length=100, unique=True)
    title = models.CharField(max_length=100, verbose_name='Име')
    kind = models.CharField(max_length=20, choices=Kind.choices, verbose_name='Вид')
    config = models.JSONField(default=dict, blank=True, verbose_name='Настройки')
    active = models.BooleanField(default=True, verbose_name='Активна')

    class Meta:
        verbose_name = 'Дигитална награда'
        verbose_name_plural = 'Дигитални награди'

    def __str__(self):
        return self.title


class UserDigitalReward(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='digital_rewards',
    )
    reward = models.ForeignKey(
        DigitalReward,
        on_delete=models.CASCADE,
        related_name='owners',
    )
    unlocked_at = models.DateTimeField(auto_now_add=True)
    source_badge = models.ForeignKey(
        Badge,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='unlocked_rewards',
    )
    selected = models.BooleanField(default=False)

    class Meta:
        verbose_name = 'Отключена дигитална награда'
        verbose_name_plural = 'Отключени дигитални награди'
        constraints = [
            models.UniqueConstraint(
                fields=('user', 'reward'),
                name='blog_unique_user_digital_reward',
            ),
        ]


class LifetimeProfileActivity(models.Model):
    class Activity(models.TextChoices):
        COMMENT = 'comment', 'Публикуван коментар'
        POLL_VOTE = 'poll_vote', 'Участие в анкета'
        MEME_APPROVED = 'meme_approved', 'Одобрено меме'
        SONG_APPROVED = 'song_approved', 'Одобрена песен'

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='lifetime_profile_activities',
        verbose_name='Потребител',
    )
    activity = models.CharField(
        max_length=20,
        choices=Activity.choices,
        verbose_name='Активност',
    )
    reference_id = models.PositiveBigIntegerField(
        verbose_name='Идентификатор на първоначалния запис',
    )
    recorded_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Зачетена на',
    )

    class Meta:
        verbose_name = 'Пожизнена профилна активност'
        verbose_name_plural = 'Пожизнени профилни активности'
        ordering = ('-recorded_at', '-pk')
        constraints = [
            models.UniqueConstraint(
                fields=('user', 'activity', 'reference_id'),
                name='blog_unique_lifetime_profile_activity',
            ),
        ]

    def __str__(self):
        return f'{self.user} — {self.get_activity_display()}'


class EmailVerificationToken(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='email_verification_tokens',
        verbose_name='Потребител',
    )
    token_hash = models.CharField(
        max_length=64,
        unique=True,
        editable=False,
        verbose_name='Хеш на токена',
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Създаден на',
    )
    expires_at = models.DateTimeField(verbose_name='Изтича на')
    used_at = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name='Използван на',
    )

    class Meta:
        verbose_name = 'Токен за потвърждение на имейл'
        verbose_name_plural = 'Токени за потвърждение на имейли'
        ordering = ('-created_at',)
        indexes = [
            models.Index(
                fields=('user', 'used_at', 'created_at'),
                name='blog_email_verify_user_idx',
            ),
            models.Index(
                fields=('expires_at',),
                name='blog_email_verify_exp_idx',
            ),
        ]

    def __str__(self):
        return f'Потвърждение за {self.user.username}'


@receiver(post_save, sender=User)
def ensure_blog_user_profile(sender, instance, created, raw=False, **kwargs):
    if created and not raw:
        UserProfile.objects.get_or_create(user=instance)


GENERAL_CATEGORY_SLUG = 'default'
MAX_POST_CATEGORIES = 3


@receiver(m2m_changed, sender=Posts.categories.through)
def enforce_post_category_limits(sender, instance, action, reverse, model, pk_set, **kwargs):
    if reverse:
        return

    if action == 'pre_clear':
        # M2M set() calls clear() first then add(). Allow clear
        # unconditionally — Общи will be re-added in post_add if missing.
        return

    if action == 'post_clear':
        general = Category.objects.filter(short_name=GENERAL_CATEGORY_SLUG).first()
        if general:
            instance.categories.add(general)
        return

    if action == 'post_remove':
        general = Category.objects.filter(short_name=GENERAL_CATEGORY_SLUG).first()
        if general and not instance.categories.filter(pk=general.pk).exists():
            instance.categories.add(general)
        return

    if action == 'pre_add':
        general = Category.objects.filter(short_name=GENERAL_CATEGORY_SLUG).first()
        if general and not instance.categories.filter(pk=general.pk).exists():
            pk_set.add(general.pk)
        current = instance.categories.count()
        added = sum(
            1 for pk in (pk_set or set())
            if not instance.categories.filter(pk=pk).exists()
        )
        if current + added > MAX_POST_CATEGORIES:
            raise ValidationError(
                f'Могат да бъдат избрани най-много {MAX_POST_CATEGORIES} категории.'
            )


# Create your models here.

class ConsentRecord(models.Model):
    # Данни за потребителя
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, help_text="Потребителят, дал съгласие (ако е логнат).")
    ip_address = models.CharField(max_length=45, blank=True, null=True, help_text="IP адрес на потребителя.")
    network_operator = models.CharField(max_length=255, blank=True, default='', help_text="Организацията/операторът за IP адреса според локалната MaxMind ASN база.")
    network_asn = models.BigIntegerField(blank=True, null=True, help_text="Autonomous System Number (ASN) за IP адреса.")

    # Резултат
    STATUS_CHOICES = [
        ('INFORMED', 'Информиран (Необходими)'),
        ('ACCEPTED', 'Приел'),
        ('REJECTED', 'Отказал'),
    ]
    consent_status = models.CharField(max_length=10, choices=STATUS_CHOICES, help_text="Статус на даденото съгласие.")

    # Доказателство
    timestamp = models.DateTimeField(auto_now_add=True, help_text="Дата и час на записа на съгласието. Формат: YYYY-MM-DD HH:MM:SS.")
    policy_version = models.CharField(max_length=50, default='v1.0', help_text="Версия на политиката за бисквитки, за която е дадено съгласието.")

    def __str__(self):
        return f"Consent: {self.consent_status} at {self.timestamp.strftime('%Y-%m-%d %H:%M')}"


class Cookie(models.Model):
    """Legacy empty model retained for migration and admin compatibility."""
    pass


# Backwards compatibility for older imports without nesting a registered model.
Cookie.ConsentRecord = ConsentRecord


class PollQuestion(models.Model):
    title = models.CharField(max_length=255, verbose_name="Заглавие", help_text="Заглавието на анкетата.")
    subtitle = models.CharField(max_length=255, blank=True, null=True, verbose_name="Подзаглавие", help_text="Подзаглавие на анкетата (по избор).")
    task_description = models.TextField(verbose_name="Описание на задачата", help_text="Текст на въпроса или описание на задачата.", blank=True, default='')
    image = models.ImageField(upload_to=poll_media_upload_path, blank=True, null=True, validators=[validate_image_upload_size], verbose_name="Изображение към въпроса", help_text="Изображение, което се показва с въпроса.")
    start_date = models.DateTimeField(verbose_name="Начална дата", default=timezone.now, help_text="Дата и час, от които анкетата е активна. Формат: YYYY-MM-DD HH:MM:SS.")
    end_date = models.DateTimeField(verbose_name="Крайна дата", default=default_poll_end_date, help_text="Дата и час, до които анкетата е активна. Формат: YYYY-MM-DD HH:MM:SS.")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Създаден на", help_text="Дата и час на създаване на анкетата. Формат: YYYY-MM-DD HH:MM:SS.")
    activation_notified_at = models.DateTimeField(
        null=True,
        blank=True,
        editable=False,
        verbose_name="Известие за активиране изпратено на",
    )

    def __str__(self):
        return self.title

    def clean(self):
        super().clean()
        if self.start_date and self.end_date and self.end_date <= self.start_date:
            raise ValidationError({
                'end_date': "Крайната дата трябва да е след началната дата."
            })

    class Meta:
        verbose_name = "Въпрос за анкета"
        verbose_name_plural = "Въпроси за анкети"
        ordering = ['-start_date']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_date__gt=models.F('start_date')),
                name='blog_poll_end_after_start',
            ),
        ]


class PollOption(models.Model):
    question = models.ForeignKey(PollQuestion, related_name='options', on_delete=models.CASCADE, verbose_name="Въпрос", help_text="Въпросът, към който принадлежи тази опция.")
    text = models.CharField(max_length=255, verbose_name="Текст на опция", help_text="Текстът на отговора.")
    image = models.ImageField(upload_to=poll_media_upload_path, blank=True, null=True, validators=[validate_image_upload_size], verbose_name="Изображение към опция", help_text="Изображение, което се показва с тази опция.")
    is_correct = models.BooleanField(default=False, verbose_name="Правилен отговор", help_text="Отбележете, ако това е правилният отговор на въпроса.")
    key = models.CharField(max_length=1, choices=[('a', 'A'), ('b', 'B'), ('c', 'C'), ('d', 'D')], verbose_name="Ключ", help_text="Буквеният ключ за тази опция (a, b, c, d).")

    def __str__(self):
        return f"{self.question.title} - {self.text}"

    class Meta:
        verbose_name = "Опция за анкета"
        verbose_name_plural = "Опции за анкети"
        unique_together = ('question', 'key')
        ordering = ['key']


class PollAnswer(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Потребител",
        help_text="Потребителят, който е отговорил.",
    )
    question = models.ForeignKey(PollQuestion, on_delete=models.CASCADE, verbose_name="Въпрос", help_text="Въпросът, на който е отговорено.")
    selected_option = models.ForeignKey(PollOption, on_delete=models.RESTRICT, verbose_name="Избрана опция", help_text="Опцията, която потребителят е избрал.")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Отговорено на", help_text="Дата и час на отговаряне. Формат: YYYY-MM-DD HH:MM:SS.")

    def __str__(self):
        username = self.user.username if self.user else "изтрит потребител"
        return f"{username} answered {self.question.title}"

    def clean(self):
        super().clean()
        if (
            self.question_id
            and self.selected_option_id
            and self.selected_option.question_id != self.question_id
        ):
            raise ValidationError({
                'selected_option': "Избраната опция не принадлежи към въпроса."
            })

    def save(self, *args, **kwargs):
        raw = kwargs.pop('raw', False)
        if not raw:
            self.full_clean()
        return super().save(*args, **kwargs)

    class Meta:
        verbose_name = "Отговор на анкета"
        verbose_name_plural = "Отговори на анкети"
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=('user', 'question'),
                name='blog_unique_poll_answer_per_user',
            ),
        ]


class ContactSubmission(models.Model):
    REASON_CHOICES = [
        ('general', 'Общо запитване'),
        ('suggestion', 'Предложение за блога'),
        ('technical_issue', 'Технически проблем'),
        ('event_question', 'Въпрос за събитие'),
        ('bug_report', 'Открит бъг'),
        ('partnership', 'Партньорство/Сътрудничество'),

    ]
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Потребител", help_text="Потребителят, изпратил съобщението (ако е логнат).")
    name = models.CharField(max_length=100, verbose_name="Име", help_text="Име на изпращача.")
    email = models.EmailField(verbose_name="Имейл", help_text="Имейл адрес на изпращача.")
    reason = models.CharField(
        max_length=20,
        choices=REASON_CHOICES,
        default='general',
        verbose_name="Причина за контакт",
        help_text="Причината, поради която потребителят се свързва."
    )
    message = models.TextField(verbose_name="Съобщение", help_text="Съдържание на съобщението.")
    submitted_at = models.DateTimeField(auto_now_add=True, verbose_name="Изпратено на", help_text="Дата и час на изпращане. Формат: YYYY-MM-DD HH:MM:SS.")

    def __str__(self):
        return f"Съобщение от {self.name} ({self.email}) относно '{self.get_reason_display()}'"

    class Meta:
        verbose_name = "Изпратен контактен формуляр"
        verbose_name_plural = "Изпратени контактни формуляри"
        ordering = ['-submitted_at']


class Notification(models.Model):
    class Audience(models.TextChoices):
        PUBLIC = 'public', 'Всички посетители'
        AUTHENTICATED = 'authenticated', 'Само регистрирани потребители'

    class SourcePolicy(models.TextChoices):
        DEACTIVATE = 'deactivate', 'Скрий при недостъпен източник'
        DETACH = 'detach', 'Запази историята без връзка'

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='notifications',
        verbose_name='Получател',
        help_text='Празно поле означава общо известие за всички.',
    )
    text = models.TextField(verbose_name="Текст на известието", help_text="Съдържанието на известието, което ще се показва на потребителите.")
    enabled = models.BooleanField(default=True, verbose_name="Активирано", help_text="Отбележете, за да активирате и покажете известието на сайта.")
    category = models.CharField(max_length=30, blank=True, default='general')
    action_url = models.CharField(max_length=255, blank=True, default='')
    audience = models.CharField(
        max_length=20,
        choices=Audience.choices,
        default=Audience.PUBLIC,
        verbose_name='Аудитория',
        help_text='Използва се само когато няма избран конкретен получател.',
    )
    deduplication_key = models.CharField(
        max_length=160,
        unique=True,
        null=True,
        blank=True,
        editable=False,
        verbose_name='Ключ срещу повторно изпращане',
    )
    source_content_type = models.ForeignKey(
        ContentType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
        editable=False,
        verbose_name='Тип на източника',
    )
    source_object_id = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        editable=False,
        verbose_name='ID на източника',
    )
    source_object = GenericForeignKey(
        'source_content_type',
        'source_object_id',
        for_concrete_model=False,
    )
    source_policy = models.CharField(
        max_length=12,
        choices=SourcePolicy.choices,
        blank=True,
        default='',
        editable=False,
        verbose_name='Поведение при недостъпен източник',
    )
    source_unavailable_at = models.DateTimeField(
        null=True,
        blank=True,
        editable=False,
        verbose_name='Източникът е недостъпен от',
    )
    important = models.BooleanField(
        default=False,
        verbose_name='Важно / без автоматично архивиране',
        help_text=(
            'Важните известия остават активни, докато не бъдат изключени '
            'или архивирани ръчно.'
        ),
    )
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name='Автоматично архивиране на',
        help_text=(
            'След тази дата известието се скрива от камбанката и се премества '
            'в архива. Оставете празно за автоматичен срок според категорията.'
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Създадено на", help_text="Дата и час на създаване на известието. Формат: YYYY-MM-DD HH:MM:SS.")

    def __str__(self):
        return self.text

    def save(self, *args, **kwargs):
        from .notification_retention import default_notification_expiry

        expiry_changed = False
        if self.important:
            if self.expires_at is not None:
                self.expires_at = None
                expiry_changed = True
        elif self.expires_at is None:
            self.expires_at = default_notification_expiry(
                self.category,
                created_at=self.created_at,
            )
            expiry_changed = True

        update_fields = kwargs.get('update_fields')
        if expiry_changed and update_fields is not None:
            kwargs['update_fields'] = tuple(set(update_fields) | {'expires_at'})
        super().save(*args, **kwargs)

    class Meta:
        verbose_name = "Известие"
        verbose_name_plural = "Известия"
        ordering = ['-created_at']
        indexes = [
            models.Index(
                fields=('source_content_type', 'source_object_id'),
                name='notification_source_idx',
            ),
        ]


class NotificationReadState(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='notification_read_states',
        verbose_name='Потребител',
    )
    notification = models.ForeignKey(
        Notification,
        on_delete=models.CASCADE,
        related_name='read_states',
        verbose_name='Известие',
    )
    read_at = models.DateTimeField(auto_now=True, verbose_name='Прочетено на')
    archived_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Архивирано на',
    )

    class Meta:
        verbose_name = 'Прочетено известие'
        verbose_name_plural = 'Прочетени известия'
        ordering = ('-read_at',)
        constraints = [
            models.UniqueConstraint(
                fields=('user', 'notification'),
                name='blog_unique_notification_read_state',
            ),
        ]
        indexes = [
            models.Index(
                fields=('user', 'read_at'),
                name='notification_read_user_idx',
            ),
            models.Index(
                fields=('user', 'archived_at'),
                name='notification_archive_user_idx',
            ),
        ]

    def __str__(self):
        return f'{self.user} — {self.notification_id}'


class TeamChatMessage(models.Model):
    author = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='team_chat_messages',
        verbose_name='Автор',
    )
    author_name = models.CharField(
        max_length=300,
        verbose_name='Име на автора при изпращане',
    )
    text = models.TextField(
        blank=True,
        max_length=4000,
        verbose_name='Съобщение',
    )
    reply_to = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='replies',
        verbose_name='Отговор на',
    )
    mentions = models.ManyToManyField(
        User,
        blank=True,
        related_name='team_chat_mentions',
        verbose_name='Споменати потребители',
    )
    discussion = models.ForeignKey(
        'TeamChatDiscussion',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='messages',
        verbose_name='Дискусия',
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        verbose_name='Изпратено на',
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        db_index=True,
        verbose_name='Обновено на',
    )
    edited_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Редактирано на',
    )
    deleted_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name='Изтрито на',
    )
    deleted_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='deleted_team_chat_messages',
        verbose_name='Изтрито от',
    )
    is_pinned = models.BooleanField(
        default=False,
        db_index=True,
        verbose_name='Закачено',
    )
    pinned_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Закачено на',
    )
    pinned_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='pinned_team_chat_messages',
        verbose_name='Закачено от',
    )

    class Meta:
        verbose_name = 'Съобщение в екипния чат'
        verbose_name_plural = 'Съобщения в екипния чат'
        ordering = ('created_at', 'pk')
        default_permissions = ()

    def __str__(self):
        return f'{self.author_name}: {self.text[:80]}'


class TeamChatSharedObject(models.Model):
    message = models.ForeignKey(
        TeamChatMessage,
        on_delete=models.CASCADE,
        related_name='shared_objects',
        verbose_name='Съобщение',
    )
    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        related_name='+',
        verbose_name='Тип на записа',
    )
    object_id = models.CharField(
        max_length=64,
        verbose_name='ID на записа',
    )
    content_object = GenericForeignKey(
        'content_type',
        'object_id',
        for_concrete_model=False,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Споделен запис в екипния чат'
        verbose_name_plural = 'Споделени записи в екипния чат'
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(
                fields=('message', 'content_type', 'object_id'),
                name='unique_team_chat_shared_obj',
            ),
        ]
        indexes = [
            models.Index(
                fields=('content_type', 'object_id'),
                name='team_chat_shared_obj_idx',
            ),
        ]

    def __str__(self):
        return f'{self.content_type_id}:{self.object_id}'


class TeamChatReaction(models.Model):
    class Emoji(models.TextChoices):
        LIKE = '👍', 'Харесвам'
        DONE = '✅', 'Готово'
        SEEN = '👀', 'Прегледано'
        IMPORTANT = '❗', 'Важно'

    message = models.ForeignKey(
        TeamChatMessage,
        on_delete=models.CASCADE,
        related_name='reactions',
        verbose_name='Съобщение',
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='team_chat_reactions',
        verbose_name='Потребител',
    )
    emoji = models.CharField(
        max_length=8,
        choices=Emoji.choices,
        verbose_name='Реакция',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Реакция в екипния чат'
        verbose_name_plural = 'Реакции в екипния чат'
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(
                fields=('message', 'user', 'emoji'),
                name='unique_team_chat_reaction',
            ),
        ]

    def __str__(self):
        return f'{self.message_id}:{self.user_id}:{self.emoji}'


class TeamChatMessageRevision(models.Model):
    class Event(models.TextChoices):
        EDITED = 'edited', 'Редактирано'
        DELETED = 'deleted', 'Изтрито'
        PINNED = 'pinned', 'Закачено'
        UNPINNED = 'unpinned', 'Откачено'

    message = models.ForeignKey(
        TeamChatMessage,
        on_delete=models.CASCADE,
        related_name='revisions',
        verbose_name='Съобщение',
    )
    event = models.CharField(
        max_length=12,
        choices=Event.choices,
        verbose_name='Действие',
    )
    previous_text = models.TextField(
        blank=True,
        verbose_name='Предишен текст',
    )
    actor = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='team_chat_revision_actions',
        verbose_name='Извършено от',
    )
    actor_name = models.CharField(
        max_length=300,
        verbose_name='Име при извършване',
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Извършено на',
    )

    class Meta:
        verbose_name = 'Ревизия на съобщение в екипния чат'
        verbose_name_plural = 'Ревизии на съобщенията в екипния чат'
        ordering = ('-created_at', '-pk')
        default_permissions = ()

    def __str__(self):
        return f'{self.get_event_display()} — {self.message_id}'


class TeamChatReadState(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='team_chat_read_state',
        verbose_name='Потребител',
    )
    last_read_message_id = models.PositiveBigIntegerField(
        default=0,
        verbose_name='Последно прочетено съобщение',
    )
    read_at = models.DateTimeField(
        auto_now=True,
        verbose_name='Прочетено на',
    )

    class Meta:
        verbose_name = 'Състояние на екипния чат'
        verbose_name_plural = 'Състояния на екипния чат'
        default_permissions = ()

    def __str__(self):
        return f'{self.user} — {self.last_read_message_id}'


class TeamChatDiscussion(models.Model):
    class Status(models.TextChoices):
        OPEN = 'open', 'Отворена'
        RESOLVED = 'resolved', 'Приключена'

    class Visibility(models.TextChoices):
        TEAM = 'team', 'Целият екип'
        GROUPS = 'groups', 'Избрани роли'
        PRIVATE = 'private', 'Конкретни хора'

    title = models.CharField(
        max_length=200,
        verbose_name='Заглавие',
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_team_chat_discussions',
        verbose_name='Създадена от',
    )
    created_by_name = models.CharField(
        max_length=300,
        verbose_name='Име на създателя',
    )
    participants = models.ManyToManyField(
        User,
        blank=True,
        related_name='team_chat_discussions',
        verbose_name='Участници',
    )
    visibility = models.CharField(
        max_length=12,
        choices=Visibility.choices,
        default=Visibility.TEAM,
        db_index=True,
        verbose_name='Достъп',
    )
    allowed_groups = models.ManyToManyField(
        Group,
        blank=True,
        related_name='allowed_team_chat_discussions',
        verbose_name='Роли с достъп',
    )
    allowed_users = models.ManyToManyField(
        User,
        blank=True,
        related_name='allowed_team_chat_discussions',
        verbose_name='Потребители с достъп',
    )
    origin_message = models.OneToOneField(
        TeamChatMessage,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='origin_discussion',
        verbose_name='Начално съобщение',
    )
    source_content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='+',
        verbose_name='Тип на административния запис',
    )
    source_object_id = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        verbose_name='ID на административния запис',
    )
    source_object = GenericForeignKey(
        'source_content_type',
        'source_object_id',
        for_concrete_model=False,
    )
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.OPEN,
        db_index=True,
        verbose_name='Състояние',
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Създадена на',
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        db_index=True,
        verbose_name='Обновена на',
    )
    resolved_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Приключена на',
    )
    resolved_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='resolved_team_chat_discussions',
        verbose_name='Приключена от',
    )
    resolved_by_name = models.CharField(
        max_length=300,
        blank=True,
        verbose_name='Име при приключване',
    )

    class Meta:
        verbose_name = 'Дискусия в екипния чат'
        verbose_name_plural = 'Дискусии в екипния чат'
        ordering = ('-updated_at', '-pk')
        default_permissions = ()
        permissions = (
            (
                'manage_team_chat_discussion_access',
                'Може да управлява достъпа до екипни дискусии',
            ),
        )
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(
                        source_content_type__isnull=True,
                        source_object_id__isnull=True,
                    )
                    | models.Q(
                        source_content_type__isnull=False,
                        source_object_id__isnull=False,
                    )
                ),
                name='team_chat_discussion_source_pair',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(origin_message__isnull=True)
                    | models.Q(source_content_type__isnull=True)
                ),
                name='team_discussion_single_origin',
            ),
        ]
        indexes = [
            models.Index(
                fields=('status', 'updated_at'),
                name='team_discussion_status_idx',
            ),
            models.Index(
                fields=('source_content_type', 'source_object_id'),
                name='team_discussion_source_idx',
            ),
        ]

    def __str__(self):
        return self.title


class TeamChatDiscussionReadState(models.Model):
    discussion = models.ForeignKey(
        TeamChatDiscussion,
        on_delete=models.CASCADE,
        related_name='read_states',
        verbose_name='Дискусия',
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='team_chat_discussion_read_states',
        verbose_name='Потребител',
    )
    last_read_message_id = models.PositiveBigIntegerField(
        default=0,
        verbose_name='Последно прочетено съобщение',
    )
    read_at = models.DateTimeField(
        auto_now=True,
        verbose_name='Прочетено на',
    )

    class Meta:
        verbose_name = 'Прочит на екипна дискусия'
        verbose_name_plural = 'Прочити на екипни дискусии'
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(
                fields=('discussion', 'user'),
                name='unique_team_discussion_read',
            ),
        ]

    def __str__(self):
        return f'{self.discussion_id}:{self.user_id}:{self.last_read_message_id}'


class TeamChatAttachment(models.Model):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    message = models.ForeignKey(
        TeamChatMessage,
        on_delete=models.CASCADE,
        related_name='attachments',
        verbose_name='Съобщение',
    )
    uploaded_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='team_chat_attachments',
        verbose_name='Качено от',
    )
    uploaded_by_name = models.CharField(
        max_length=300,
        verbose_name='Име на качилия',
    )
    image = models.ImageField(
        storage=team_chat_protected_storage,
        upload_to=team_chat_attachment_upload_path,
        width_field='width',
        height_field='height',
        verbose_name='Изображение',
    )
    thumbnail = models.ImageField(
        storage=team_chat_protected_storage,
        upload_to=team_chat_attachment_thumbnail_upload_path,
        verbose_name='Миниатюра',
    )
    original_name = models.CharField(
        max_length=255,
        blank=True,
        verbose_name='Оригинално име',
    )
    original_format = models.CharField(
        max_length=12,
        verbose_name='Оригинален формат',
    )
    content_type = models.CharField(
        max_length=40,
        default='image/webp',
        verbose_name='MIME тип',
    )
    byte_size = models.PositiveIntegerField(
        verbose_name='Размер в байтове',
    )
    width = models.PositiveIntegerField(verbose_name='Ширина')
    height = models.PositiveIntegerField(verbose_name='Височина')
    is_animated = models.BooleanField(
        default=False,
        verbose_name='Анимирано',
    )
    alt_text = models.CharField(
        max_length=255,
        blank=True,
        verbose_name='Алтернативен текст',
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        verbose_name='Качено на',
    )

    class Meta:
        verbose_name = 'Изображение в екипния чат'
        verbose_name_plural = 'Изображения в екипния чат'
        ordering = ('created_at', 'pk')
        default_permissions = ()
        indexes = [
            models.Index(
                fields=('uploaded_by', 'created_at'),
                name='team_attachment_rate_idx',
            ),
        ]

    def __str__(self):
        return f'{self.message_id}: {self.original_name or self.pk}'


class NotificationPreference(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='notification_preferences',
        verbose_name='Потребител',
    )
    general_enabled = models.BooleanField(
        default=True,
        verbose_name='Официални известия',
    )
    points_enabled = models.BooleanField(
        default=True,
        verbose_name='Получени точки',
    )
    levels_enabled = models.BooleanField(
        default=True,
        verbose_name='Нови нива',
    )
    badges_enabled = models.BooleanField(
        default=True,
        verbose_name='Получени значки',
    )
    missions_enabled = models.BooleanField(
        default=True,
        verbose_name='Завършени мисии',
    )
    rewards_enabled = models.BooleanField(
        default=True,
        verbose_name='Дигитални награди',
    )
    posts_enabled = models.BooleanField(default=True, verbose_name='Нови публикации')
    events_enabled = models.BooleanField(default=True, verbose_name='Събития и напомняния')
    polls_enabled = models.BooleanField(default=True, verbose_name='Нови анкети')
    changelog_enabled = models.BooleanField(default=True, verbose_name='Промени по сайта')
    comment_replies_enabled = models.BooleanField(default=True, verbose_name='Отговори на коментари')
    submissions_enabled = models.BooleanField(default=True, verbose_name='Решения за предложения')
    mission_available_enabled = models.BooleanField(default=True, verbose_name='Нови мисии')
    security_enabled = models.BooleanField(default=True, verbose_name='Сигурност на профила')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Обновени на')

    class Meta:
        verbose_name = 'Настройки за известия'
        verbose_name_plural = 'Настройки за известия'

    def __str__(self):
        return f'Известия на {self.user}'


class PushSubscription(models.Model):
    """A browser push endpoint, optionally linked to an authenticated user."""

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='push_subscriptions',
        verbose_name='Потребител',
        null=True,
        blank=True,
    )
    # Browser endpoints can be much longer than MySQL's safe indexed varchar
    # length under utf8mb4. Store the full value unindexed and index its digest.
    endpoint = models.TextField(verbose_name='Push endpoint')
    endpoint_hash = models.CharField(
        max_length=64,
        unique=True,
        editable=False,
        verbose_name='SHA-256 на Push endpoint-а',
    )
    management_token_hash = models.CharField(
        max_length=64,
        blank=True,
        editable=False,
        verbose_name='SHA-256 на токена за управление',
    )
    p256dh = models.CharField(max_length=255, verbose_name='Публичен ключ на браузъра')
    auth = models.CharField(max_length=255, verbose_name='Auth ключ на браузъра')
    user_agent = models.CharField(max_length=255, blank=True, verbose_name='Браузър')
    active = models.BooleanField(default=True, verbose_name='Активен')
    general_enabled = models.BooleanField(default=True, verbose_name='Официални известия')
    posts_enabled = models.BooleanField(default=True, verbose_name='Нови публикации')
    events_enabled = models.BooleanField(default=True, verbose_name='Събития и напомняния')
    polls_enabled = models.BooleanField(default=True, verbose_name='Нови анкети')
    changelog_enabled = models.BooleanField(default=True, verbose_name='Промени по сайта')
    failure_count = models.PositiveSmallIntegerField(default=0, verbose_name='Поредни грешки')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Създаден на')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Обновен на')
    last_success_at = models.DateTimeField(null=True, blank=True, verbose_name='Последно успешно изпращане')

    class Meta:
        verbose_name = 'Push абонамент'
        verbose_name_plural = 'Push абонаменти'
        ordering = ('-updated_at',)
        indexes = [
            models.Index(fields=('user', 'active'), name='push_user_active_idx'),
        ]

    def __str__(self):
        owner = self.user or 'Анонимно устройство'
        return f'{owner} — {self.endpoint[:60]}'

    @staticmethod
    def digest_endpoint(endpoint):
        return hashlib.sha256(endpoint.encode('utf-8')).hexdigest()

    @staticmethod
    def digest_management_token(token):
        return hashlib.sha256(token.encode('utf-8')).hexdigest()

    def management_token_matches(self, token):
        if not token or not self.management_token_hash:
            return False
        return secrets.compare_digest(
            self.management_token_hash,
            self.digest_management_token(token),
        )

    def save(self, *args, **kwargs):
        self.endpoint_hash = self.digest_endpoint(self.endpoint)
        return super().save(*args, **kwargs)


class PushDelivery(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Изчаква'
        SENT = 'sent', 'Изпратено'
        FAILED = 'failed', 'Неуспешно'
        CANCELLED = 'cancelled', 'Отказано'

    subscription = models.ForeignKey(
        PushSubscription,
        on_delete=models.CASCADE,
        related_name='deliveries',
        verbose_name='Push абонамент',
    )
    notification = models.ForeignKey(
        Notification,
        on_delete=models.CASCADE,
        related_name='push_deliveries',
        verbose_name='Известие',
    )
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
        verbose_name='Статус',
    )
    attempts = models.PositiveSmallIntegerField(default=0, verbose_name='Опити')
    last_attempt_at = models.DateTimeField(null=True, blank=True, verbose_name='Последен опит')
    sent_at = models.DateTimeField(null=True, blank=True, verbose_name='Изпратено на')
    cancelled_at = models.DateTimeField(null=True, blank=True, verbose_name='Отказано на')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Създадено на')

    class Meta:
        verbose_name = 'Push доставка'
        verbose_name_plural = 'Push доставки'
        constraints = [
            models.UniqueConstraint(
                fields=('subscription', 'notification'),
                name='blog_unique_push_delivery',
            ),
        ]
        indexes = [
            models.Index(fields=('status', 'created_at'), name='push_delivery_status_idx'),
        ]

    def __str__(self):
        return f'{self.notification_id} → {self.subscription_id} ({self.status})'


class GamificationNotification(Notification):
    """Admin-only proxy used to keep automatic game events separate."""

    class Meta:
        proxy = True
        verbose_name = 'Известие от геймификацията'
        verbose_name_plural = 'Известия от геймификацията'
        ordering = ['-created_at']


class TermsOfService(models.Model):
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Автор", help_text="Авторът, който е създал/редактирал условията.")
    content = models.TextField(null=False, help_text="Пълният текст на Условията за ползване. Поддържа се Markdown.")
    date = models.DateTimeField(auto_now_add=True, help_text="Дата на последната промяна. Формат: YYYY-MM-DD HH:MM:SS.")

    def __str__(self):
        username = self.user.username if self.user else "изтрит потребител"
        return f"{self.content} качено на : {username}"

    class Meta:
        verbose_name = "Условие за ползване"
        verbose_name_plural = "Условия за ползване"

class PrivacyPolicy(models.Model):
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Автор", help_text="Авторът, който е създал/редактирал политиката.")
    content = models.TextField(null=False, help_text="Пълният текст на Политиката за поверителност. Поддържа се Markdown.")
    date = models.DateTimeField(auto_now_add=True, help_text="Дата на последната промяна. Формат: YYYY-MM-DD HH:MM:SS.")

    def __str__(self):
        username = self.user.username if self.user else "изтрит потребител"
        return f"{self.content} качено на : {username}"

    class Meta:
        verbose_name = "Политика за поверителност"
        verbose_name_plural = "Политики за поверителност"

class PostImage(models.Model):
    post = models.ForeignKey(Posts, related_name='images', on_delete=models.CASCADE, help_text="Публикацията, към която е свързана тази снимка.")
    image = models.ImageField(upload_to=post_gallery_upload_path, validators=[validate_image_upload_size], help_text="Файлът с изображението.")
    title = models.CharField(
        max_length=150,
        blank=True,
        verbose_name="Заглавие",
        help_text="Кратко видимо заглавие или описание под снимката.",
    )
    alt_text = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Алтернативен текст",
        help_text="Опишете какво има на снимката за екранни четци и търсачки.",
    )
    position = models.PositiveIntegerField(
        default=0,
        verbose_name="Позиция",
        help_text="Редът на снимката в галерията.",
    )
    is_featured = models.BooleanField(
        default=False,
        verbose_name="Водеща снимка",
        help_text="Водещата снимка се показва по-голяма и първа в галерията.",
    )
    focus_x = models.PositiveSmallIntegerField(
        default=50,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        verbose_name="Хоризонтален фокус (%)",
        help_text="0 е левият край, 50 е центърът, а 100 е десният край.",
    )
    focus_y = models.PositiveSmallIntegerField(
        default=50,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        verbose_name="Вертикален фокус (%)",
        help_text="0 е горният край, 50 е центърът, а 100 е долният край.",
    )
    file_hash = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        editable=False,
        verbose_name="SHA-256 отпечатък",
    )

    def __str__(self):
        return f"Image for post: {self.post.title}"

    class Meta:
        verbose_name = "Снимка към публикация"
        verbose_name_plural = "Снимки към публикации"
        ordering = ('position', 'pk')
        constraints = [
            models.UniqueConstraint(
                fields=('post', 'file_hash'),
                name='unique_post_image_content',
            ),
            models.CheckConstraint(
                condition=models.Q(focus_x__gte=0, focus_x__lte=100),
                name='post_image_focus_x_0_100',
            ),
            models.CheckConstraint(
                condition=models.Q(focus_y__gte=0, focus_y__lte=100),
                name='post_image_focus_y_0_100',
            ),
        ]

    def save(self, *args, **kwargs):
        if self.image:
            is_new_file = not getattr(self.image, '_committed', True)
            if not self.file_hash or is_new_file:
                uploaded_file = getattr(self.image, 'file', None)
                self.file_hash = (
                    getattr(uploaded_file, '_post_image_sha256', None)
                    or calculate_file_sha256(self.image)
                )

        update_fields = kwargs.get('update_fields')
        if update_fields is not None and self.file_hash:
            kwargs['update_fields'] = tuple(set(update_fields) | {'file_hash'})

        with transaction.atomic():
            if (
                self.post_id
                and self.file_hash
                and type(self).objects.filter(
                    post_id=self.post_id,
                    file_hash=self.file_hash,
                ).exclude(pk=self.pk).exists()
            ):
                raise ValidationError({
                    'image': 'Тази снимка вече присъства в галерията.'
                })
            if self.is_featured and self.post_id:
                type(self).objects.filter(
                    post_id=self.post_id,
                    is_featured=True,
                ).exclude(pk=self.pk).update(is_featured=False)
            return super().save(*args, **kwargs)

class PostDocument(models.Model):
    post = models.ForeignKey(Posts, related_name='documents', on_delete=models.CASCADE, help_text="Публикацията, към която е свързан този документ.")
    file = models.FileField(
        upload_to=post_document_upload_path,
        validators=[
            FileExtensionValidator(allowed_extensions=DOCUMENT_EXTENSIONS),
            validate_document_upload_size,
        ],
        help_text="Файлът с документа (PDF, DOCX, DOC, XLSX, XLS, PPTX, PPT, ZIP, RAR, 7Z, CSV, TXT, JSON, RTF).",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True, help_text="Дата и час на качване на документа. Формат: YYYY-MM-DD HH:MM:SS.")

    def __str__(self):
        return self.file.name

    class Meta:
        verbose_name = "Документ към публикация"
        verbose_name_plural = "Документи към публикации"


class Event(models.Model):
    title = models.CharField(max_length=255, verbose_name="Заглавие", help_text="Име на събитието.")
    start_datetime = models.DateTimeField(verbose_name="Начална дата и час", help_text="Кога започва събитието. Формат: YYYY-MM-DD HH:MM:SS.")
    end_datetime = models.DateTimeField(blank=True, null=True, verbose_name="Крайна дата и час (по избор)", help_text="Кога приключва събитието (ако е приложимо). Формат: YYYY-MM-DD HH:MM:SS.")
    location = models.CharField(max_length=255, verbose_name="Местоположение", help_text="Къде ще се проведе събитието.")
    category = models.CharField(
        max_length=100,
        choices=[
            ('Academic', 'Академично'),
            ('Sports', 'Спортно'),
            ('Arts', 'Изкуства'),
            ('Community', 'Общностно'),
            ('Holiday', 'Празник'),
            ('Other', 'Друго'),
        ],
        verbose_name="Категория",
        help_text="Тип на събитието.",
    )
    description = models.TextField(verbose_name="Описание", help_text="Подробно описание на събитието. Поддържа се Markdown.")
    attendees_text = models.CharField(max_length=255, verbose_name="Участници (текст)", help_text="Кой може да присъства (напр. 'Всички ученици', '8-12 клас').")
    published = models.BooleanField(default=True, verbose_name="Публикувано", help_text="Отбележете, за да се показва събитието на сайта.")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Създадено на", help_text="Дата на създаване на записа за събитието. Формат: YYYY-MM-DD HH:MM:SS.")
    publication_notified_at = models.DateTimeField(
        null=True,
        blank=True,
        editable=False,
        verbose_name="Известие за публикуване изпратено на",
    )
    reminder_notified_at = models.DateTimeField(
        null=True,
        blank=True,
        editable=False,
        verbose_name="Напомняне изпратено на",
    )

    def __str__(self):
        return self.title

    def clean(self):
        super().clean()
        if (
            self.start_datetime
            and self.end_datetime
            and self.end_datetime <= self.start_datetime
        ):
            raise ValidationError({
                'end_datetime': "Краят на събитието трябва да е след началото."
            })

    class Meta:
        verbose_name = "Събитие"
        verbose_name_plural = "Събития"
        ordering = ['start_datetime']
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(end_datetime__isnull=True)
                    | models.Q(end_datetime__gt=models.F('start_datetime'))
                ),
                name='blog_event_end_after_start',
            ),
        ]


class ScheduleEntry(models.Model):
    """One lesson period for a class. Entries are replaced together by CSV import."""

    class_name = models.CharField(max_length=32, verbose_name="Клас")
    lesson_number = models.PositiveSmallIntegerField(verbose_name="Час")
    monday = models.CharField(max_length=255, blank=True, verbose_name="Понеделник")
    tuesday = models.CharField(max_length=255, blank=True, verbose_name="Вторник")
    wednesday = models.CharField(max_length=255, blank=True, verbose_name="Сряда")
    thursday = models.CharField(max_length=255, blank=True, verbose_name="Четвъртък")
    friday = models.CharField(max_length=255, blank=True, verbose_name="Петък")

    class Meta:
        verbose_name = "Запис в учебната програма"
        verbose_name_plural = "Учебна програма"
        ordering = ("class_name", "lesson_number")
        constraints = [
            models.UniqueConstraint(
                fields=("class_name", "lesson_number"),
                name="blog_unique_schedule_class_lesson",
            ),
        ]

    def __str__(self):
        return f"{self.class_name} — {self.lesson_number}. час"


class ScheduleImport(models.Model):
    """A restorable CSV snapshot of the school schedule."""

    source_file = models.FileField(upload_to='schedule_imports/%Y/%m/')
    imported_at = models.DateTimeField(auto_now_add=True, verbose_name="Импортирана на")
    imported_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='schedule_imports',
        verbose_name="Импортирана от",
    )
    entry_count = models.PositiveIntegerField(default=0, verbose_name="Брой редове")
    is_current = models.BooleanField(default=False, verbose_name="Текуща версия")

    class Meta:
        verbose_name = "Импорт на учебна програма"
        verbose_name_plural = "История на импортираните програми"
        ordering = ('-imported_at',)

    def __str__(self):
        return f"Програма от {self.imported_at.strftime('%d.%m.%Y %H:%M')}"


class PageView(models.Model):
    path = models.CharField(
        max_length=255,
        db_index=True,
        verbose_name='Страница',
    )
    post = models.ForeignKey(
        Posts,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='page_views',
        verbose_name='Публикация',
    )
    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='page_views',
        verbose_name='Потребител',
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = 'Преглед на страница'
        verbose_name_plural = 'Прегледи на страници'
        ordering = ('-created_at',)
        indexes = [
            models.Index(
                fields=('post', 'created_at'),
                name='page_view_post_created_idx',
            ),
        ]

    def __str__(self):
        return self.path


class Changelog(models.Model):
    content = models.TextField(help_text="Съдържание на промените в Markdown формат.")
    created_at = models.DateTimeField(auto_now_add=True, help_text="Дата на създаване.")
    updated_at = models.DateTimeField(auto_now=True, help_text="Дата на последна редакция.")
    is_active = models.BooleanField(default=True, help_text="Отбележете, за да се вземе предвид този запис.")
    notification_notified_at = models.DateTimeField(
        null=True,
        blank=True,
        editable=False,
        help_text='Кога е изпратено автоматичното известие за този запис.',
    )

    class Meta:
        verbose_name = "Запис в дневника на промените"
        verbose_name_plural = "Дневник на промените"
        ordering = ['-updated_at']

    def __str__(self):
        return f"Промени от {self.updated_at.strftime('%Y-%m-%d %H:%M')}"


class SiteSettings(models.Model):
    singleton_key = models.PositiveSmallIntegerField(
        default=1,
        unique=True,
        editable=False,
    )
    maintenance_mode = models.BooleanField(default=False, verbose_name="Режим на поддръжка", help_text="Ако е активиран, сайтът ще показва страница за поддръжка на всички потребители, които не са администратори.")
    enable_bell_suggestions = models.BooleanField(default=True, verbose_name="Активирани 'Предложения за песни'", help_text="Позволява на потребителите да предлагат песни за училищния звънец.")
    enable_weekly_poll = models.BooleanField(default=True, verbose_name="Активирана 'Седмична анкета'", help_text="Активира/деактивира показването на седмичната анкета на сайта.")
    enable_meme_of_the_week = models.BooleanField(default=True, verbose_name="Активирано 'Меме на седмицата'", help_text="Позволява на потребителите да качват и гласуват за мемета.")
    enable_user_registration = models.BooleanField(default=True, verbose_name="Активирани 'Регистрации на потребители'", help_text="Позволява на нови потребители да се регистрират в системата.")
    auto_verify_on_email_failure = models.BooleanField(
        default=False,
        verbose_name='Автоматично потвърждение при проблем с имейл услугата',
        help_text=(
            'Аварийна настройка: ако Resend не успее да изпрати писмото, '
            'новият акаунт се потвърждава автоматично. Оставете изключено '
            'при нормална работа.'
        ),
    )
    enable_program_page = models.BooleanField(default=True, verbose_name="Активирана страница 'Програма'", help_text="Показва или скрива страницата с учебната програма.")

    class Meta:
        verbose_name = "Настройки на сайта"
        verbose_name_plural = "Настройки на сайта"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(singleton_key=1),
                name='blog_site_settings_singleton_key',
            ),
        ]

    def save(self, *args, **kwargs):
        self.singleton_key = 1
        if not self.pk:
            if SiteSettings.objects.exists():
                raise ValidationError(
                    'Може да съществува само един обект с настройки на сайта.'
                )
            self.pk = 1
        return super(SiteSettings, self).save(*args, **kwargs)


class GoogleIdentity(models.Model):
    """Stable Google account binding; e-mail is metadata, never the identity key."""

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='google_identity',
    )
    issuer = models.CharField(max_length=64)
    subject = models.CharField(max_length=255)
    email_at_link_time = models.EmailField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=('issuer', 'subject'),
                name='blog_unique_google_identity',
            ),
        ]
        verbose_name = "Google самоличност"
        verbose_name_plural = "Google самоличности"

    def __str__(self):
        return f"{self.issuer}:{self.subject}"


class BlogLoginSession(models.Model):
    """Server-side state for one issued pair of blog JWT tokens."""

    AUTH_PASSWORD = 'password'
    AUTH_GOOGLE = 'google'
    AUTH_METHOD_CHOICES = (
        (AUTH_PASSWORD, 'Парола'),
        (AUTH_GOOGLE, 'Google'),
    )

    REVOKED_LOGOUT = 'logout'
    REVOKED_USER = 'user'
    REVOKED_PASSWORD_CHANGE = 'password_change'
    REVOKED_ACCOUNT_DEACTIVATED = 'account_deactivated'
    REVOKED_ADMIN = 'admin'
    REVOKED_REASON_CHOICES = (
        (REVOKED_LOGOUT, 'Изход'),
        (REVOKED_USER, 'Прекратена от потребителя'),
        (REVOKED_PASSWORD_CHANGE, 'Смяна на парола'),
        (REVOKED_ACCOUNT_DEACTIVATED, 'Деактивиран профил'),
        (REVOKED_ADMIN, 'Прекратена от администратор'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='blog_login_sessions',
        verbose_name='Потребител',
    )
    auth_method = models.CharField(
        max_length=16,
        choices=AUTH_METHOD_CHOICES,
        verbose_name='Метод за вход',
    )
    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True,
        verbose_name='IP адрес',
    )
    network_operator = models.CharField(
        max_length=255,
        blank=True,
        default='',
        verbose_name='Мрежов оператор',
    )
    network_asn = models.BigIntegerField(
        null=True,
        blank=True,
        verbose_name='ASN',
    )
    device_label = models.CharField(
        max_length=120,
        blank=True,
        default='',
        verbose_name='Устройство',
    )
    device_identifier_hash = models.CharField(
        max_length=64,
        blank=True,
        default='',
        editable=False,
        verbose_name='Хеш на идентификатора на устройството',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Вход на')
    last_seen_at = models.DateTimeField(verbose_name='Последна активност')
    expires_at = models.DateTimeField(verbose_name='Изтича на')
    revoked_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Прекратена на',
    )
    revoked_reason = models.CharField(
        max_length=32,
        choices=REVOKED_REASON_CHOICES,
        blank=True,
        default='',
        verbose_name='Причина за прекратяване',
    )
    refresh_jti = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        unique=True,
        editable=False,
    )

    class Meta:
        verbose_name = 'Сесия за вход в блога'
        verbose_name_plural = 'Сесии за вход в блога'
        ordering = ('-created_at',)
        indexes = [
            models.Index(
                fields=('user', 'revoked_at', 'expires_at'),
                name='blog_session_active_idx',
            ),
            models.Index(
                fields=('user', 'created_at'),
                name='blog_session_history_idx',
            ),
        ]

    @property
    def is_active(self):
        return self.revoked_at is None and self.expires_at > timezone.now()

    def __str__(self):
        return f'{self.user} · {self.get_auth_method_display()} · {self.created_at:%d.%m.%Y %H:%M}'


class PersonalDataCleanupRun(models.Model):
    """Audit record for one execution of the personal-data retention job."""

    STATUS_RUNNING = 'running'
    STATUS_SUCCEEDED = 'succeeded'
    STATUS_FAILED = 'failed'
    STATUS_CHOICES = (
        (STATUS_RUNNING, 'Изпълнява се'),
        (STATUS_SUCCEEDED, 'Успешно'),
        (STATUS_FAILED, 'Грешка'),
    )

    started_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Стартирано на',
    )
    finished_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Завършено на',
    )
    retention_days = models.PositiveIntegerField(
        verbose_name='Срок в дни',
    )
    cutoff_at = models.DateTimeField(
        verbose_name='Изтриване на записи преди',
    )
    dry_run = models.BooleanField(
        default=False,
        verbose_name='Само проверка',
    )
    status = models.CharField(
        max_length=12,
        choices=STATUS_CHOICES,
        default=STATUS_RUNNING,
        verbose_name='Статус',
    )
    result_counts = models.JSONField(
        default=dict,
        blank=True,
        verbose_name='Резултати по категории',
    )
    error_message = models.TextField(
        blank=True,
        default='',
        verbose_name='Грешка',
    )

    class Meta:
        verbose_name = 'Изпълнение на почистването на лични данни'
        verbose_name_plural = 'История на почистването на лични данни'
        ordering = ('-started_at',)
        indexes = [
            models.Index(
                fields=('started_at',),
                name='cleanup_run_started_idx',
            ),
            models.Index(
                fields=('status', 'started_at'),
                name='cleanup_run_status_idx',
            ),
        ]

    @property
    def affected_records(self):
        return sum(
            value
            for value in self.result_counts.values()
            if isinstance(value, int) and not isinstance(value, bool)
        )

    def __str__(self):
        return f'{self.get_status_display()} · {self.started_at:%d.%m.%Y %H:%M}'


def _sync_song_votes(song_ids):
    through = BellSongSuggestion.voted_by.through
    for song_id in song_ids:
        count = through.objects.filter(bellsongsuggestion_id=song_id).count()
        BellSongSuggestion.objects.filter(pk=song_id).update(votes=count)


def _sync_meme_votes(meme_ids):
    through = MemeOfWeek.voted_by.through
    for meme_id in meme_ids:
        count = through.objects.filter(memeofweek_id=meme_id).count()
        MemeOfWeek.objects.filter(pk=meme_id).update(votes=count)


@receiver(m2m_changed, sender=BellSongSuggestion.voted_by.through)
def sync_song_vote_counter(sender, instance, action, reverse, pk_set, **kwargs):
    cache_name = '_song_vote_ids_before_clear'
    if action == 'pre_clear' and reverse:
        setattr(instance, cache_name, list(instance.voted_songs.values_list('pk', flat=True)))
        return
    if action not in {'post_add', 'post_remove', 'post_clear'}:
        return
    if reverse:
        song_ids = pk_set or getattr(instance, cache_name, [])
    else:
        song_ids = [instance.pk]
    _sync_song_votes(song_ids)


@receiver(m2m_changed, sender=MemeOfWeek.voted_by.through)
def sync_meme_vote_counter(sender, instance, action, reverse, pk_set, **kwargs):
    cache_name = '_meme_vote_ids_before_clear'
    if action == 'pre_clear' and reverse:
        setattr(instance, cache_name, list(instance.voted_memes.values_list('pk', flat=True)))
        return
    if action not in {'post_add', 'post_remove', 'post_clear'}:
        return
    if reverse:
        meme_ids = pk_set or getattr(instance, cache_name, [])
    else:
        meme_ids = [instance.pk]
    _sync_meme_votes(meme_ids)


@receiver(pre_delete, sender=User)
def remember_vote_targets_before_user_delete(sender, instance, **kwargs):
    instance._song_vote_ids_before_delete = list(
        instance.voted_songs.values_list('pk', flat=True)
    )
    instance._meme_vote_ids_before_delete = list(
        instance.voted_memes.values_list('pk', flat=True)
    )


@receiver(post_delete, sender=User)
def sync_vote_counters_after_user_delete(sender, instance, **kwargs):
    _sync_song_votes(getattr(instance, '_song_vote_ids_before_delete', []))
    _sync_meme_votes(getattr(instance, '_meme_vote_ids_before_delete', []))
