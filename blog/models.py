import hashlib
import uuid
from datetime import timedelta
from django.conf import settings
from django.contrib.auth.models import User
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
    category = models.ForeignKey(Category, on_delete=models.PROTECT, help_text="Категорията, към която принадлежи публикацията.")
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

    class Meta:
        permissions = [
            ('view_only_posts', 'View only users posts'),
            ('can_view_all_posts', 'Can view all posts'),
            ('can_view_all_pnp_posts', 'Can view all post if they not published'),
            ('can_allow_posts', 'Can allow posts'),
            ('can_edit_users_post', 'Can edit users posts'),
        ]
        verbose_name = "Публикация"
        verbose_name_plural = "Публикации"

    def __str__(self):
        return self.title

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


@receiver(post_save, sender=User)
def ensure_blog_user_profile(sender, instance, created, raw=False, **kwargs):
    if created and not raw:
        UserProfile.objects.get_or_create(user=instance)


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
    text = models.TextField(verbose_name="Текст на известието", help_text="Съдържанието на известието, което ще се показва на потребителите.")
    enabled = models.BooleanField(default=True, verbose_name="Активирано", help_text="Отбележете, за да активирате и покажете известието на сайта.")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Създадено на", help_text="Дата и час на създаване на известието. Формат: YYYY-MM-DD HH:MM:SS.")

    def __str__(self):
        return self.text

    class Meta:
        verbose_name = "Известие"
        verbose_name_plural = "Известия"
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
    category = models.CharField(max_length=100, verbose_name="Категория", help_text="Тип на събитието (напр. 'Училищно', 'Спортно').")
    description = models.TextField(verbose_name="Описание", help_text="Подробно описание на събитието. Поддържа се Markdown.")
    attendees_text = models.CharField(max_length=255, verbose_name="Участници (текст)", help_text="Кой може да присъства (напр. 'Всички ученици', '8-12 клас').")
    published = models.BooleanField(default=True, verbose_name="Публикувано", help_text="Отбележете, за да се показва събитието на сайта.")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Създадено на", help_text="Дата на създаване на записа за събитието. Формат: YYYY-MM-DD HH:MM:SS.")

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


class Changelog(models.Model):
    content = models.TextField(help_text="Съдържание на промените в Markdown формат.")
    created_at = models.DateTimeField(auto_now_add=True, help_text="Дата на създаване.")
    updated_at = models.DateTimeField(auto_now=True, help_text="Дата на последна редакция.")
    is_active = models.BooleanField(default=True, help_text="Отбележете, за да се вземе предвид този запис.")

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
