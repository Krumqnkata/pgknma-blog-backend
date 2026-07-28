import uuid
from django.contrib.auth.models import User
from django.db import models
from django.db.models import BooleanField
from django.utils import timezone
from django.core.exceptions import ValidationError

def post_media_upload_path(instance, filename, field_type):
    """
    Generates a robust upload path for post-related media.
    Handles both new and existing post objects.
    """
    post_id = None
    if hasattr(instance, 'post') and instance.post.pk:
        # Instance is a related model (PostImage, PostDocument) with a saved parent post.
        post_id = instance.post.pk
    elif hasattr(instance, 'pk') and instance.pk:
        # Instance is the main Post model and it has been saved.
        post_id = instance.pk

    if post_id is None:
        # This handles a new, unsaved Post instance.
        # We generate a temporary UUID to group files for this new post.
        # This path will be permanent, but it prevents using 'None' or 'unknown'.
        post_id = uuid.uuid4().hex

    return f'posts/{post_id}/{field_type}/{filename}'


def post_banner_upload_path(instance, filename):
    return post_media_upload_path(instance, filename, 'banner')

def post_gallery_upload_path(instance, filename):
    return post_media_upload_path(instance, filename, 'gallery')

def post_document_upload_path(instance, filename):
    return post_media_upload_path(instance, filename, 'documents')

def meme_upload_path(instance, filename):
    return f'memes/{instance.user.username}/{filename}'

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
    category = models.ForeignKey(Category, on_delete=models.CASCADE, help_text="Категорията, към която принадлежи публикацията.")
    banner = models.ImageField(upload_to=post_banner_upload_path, blank=True, null=True, help_text="Банер изображение, което ще се показва в горната част на публикацията.")
    hook = models.TextField(max_length=100, help_text="Кратко въведение или 'кукичка', което да привлече вниманието на читателя до 100 символа.")
    content = models.TextField(blank=False, help_text="Пълното съдържание на публикацията. Поддържа се Markdown формат.")
    author = models.ForeignKey(User, on_delete=models.CASCADE, help_text="Авторът на публикацията. Полето се попълва автоматично.")
    created_at = models.DateTimeField(default=timezone.now, help_text="Дата и час на създаване на публикацията. Формат: YYYY-MM-DD HH:MM:SS.")
    published = BooleanField(help_text="Отбележете, ако публикацията трябва да бъде видима за всички потребители.")
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
    user = models.ForeignKey(User, on_delete=models.CASCADE, help_text="Потребителят, който е написал коментара.")
    created_at = models.DateTimeField(auto_now_add=True, help_text="Дата и час на създаване на коментара. Формат: YYYY-MM-DD HH:MM:SS.")
    post = models.ForeignKey(Posts, on_delete=models.CASCADE, help_text="Публикацията, към която е коментарът.")
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.CASCADE, related_name='replies', help_text="Родителският коментар, ако този е отговор.")

    class Meta:
        ordering = ['created_at']

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
    user = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name="Потребител", help_text="Потребителят, който е направил предложението.")
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

    def __str__(self):
        return f"{self.title} ({self.get_status_display()})"

class MemeOfWeek(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name="Потребител", help_text="Потребителят, който е качил мемето.")
    title = models.CharField(max_length=100, blank=True, null=True, verbose_name="Заглавие", help_text="Заглавие на мемето (по избор).")
    image = models.ImageField(upload_to=meme_upload_path, blank=True, null=True, verbose_name="Изображение", help_text="Файлът с изображението на мемето.")
    is_approved = models.BooleanField(default=False, verbose_name="Одобрено", help_text="Отбележете, ако мемето е одобрено за участие в 'Меме на седмицата'.")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата на качване", null=True, help_text="Дата и час на качване на мемето. Формат: YYYY-MM-DD HH:MM:SS.")
    voted_by = models.ManyToManyField(User, related_name='voted_memes', blank=True, verbose_name="Гласували потребители", help_text="Потребители, които са гласували за това меме.")
    votes = models.IntegerField(default=0, verbose_name="Гласове", help_text="Брой на гласовете за мемето.")

    class Meta:
        verbose_name = "Меме на седмицата"
        verbose_name_plural = "Мемета на седмицата"
        ordering = ['-created_at']

    def __str__(self):
        if self.title:
            return self.title
        return f"Meme by {self.user.username}"

class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, help_text="Свързаният потребителски акаунт.")

    def __str__(self):
        # ...
        return f"Профил на {self.user.username}"
# Create your models here.

class Cookie(models.Model):
    class ConsentRecord(models.Model):
        # Данни за потребителя
        user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, help_text="Потребителят, дал съгласие (ако е логнат).")
        ip_address = models.CharField(max_length=45, blank=True, null=True, help_text="IP адрес на потребителя.")

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


class PollQuestion(models.Model):
    title = models.CharField(max_length=255, verbose_name="Заглавие", help_text="Заглавието на анкетата.")
    subtitle = models.CharField(max_length=255, blank=True, null=True, verbose_name="Подзаглавие", help_text="Подзаглавие на анкетата (по избор).")
    task_description = models.TextField(verbose_name="Описание на задачата", help_text="Текст на въпроса или описание на задачата.", blank=True, default='')
    image = models.ImageField(upload_to=poll_media_upload_path, blank=True, null=True, verbose_name="Изображение към въпроса", help_text="Изображение, което се показва с въпроса.")
    start_date = models.DateTimeField(verbose_name="Начална дата", default=timezone.now, help_text="Дата и час, от които анкетата е активна. Формат: YYYY-MM-DD HH:MM:SS.")
    end_date = models.DateTimeField(verbose_name="Крайна дата", default=timezone.now, help_text="Дата и час, до които анкетата е активна. Формат: YYYY-MM-DD HH:MM:SS.")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Създаден на", help_text="Дата и час на създаване на анкетата. Формат: YYYY-MM-DD HH:MM:SS.")

    def __str__(self):
        return self.title

    class Meta:
        verbose_name = "Въпрос за анкета"
        verbose_name_plural = "Въпроси за анкети"
        ordering = ['-start_date']


class PollOption(models.Model):
    question = models.ForeignKey(PollQuestion, related_name='options', on_delete=models.CASCADE, verbose_name="Въпрос", help_text="Въпросът, към който принадлежи тази опция.")
    text = models.CharField(max_length=255, verbose_name="Текст на опция", help_text="Текстът на отговора.")
    image = models.ImageField(upload_to=poll_media_upload_path, blank=True, null=True, verbose_name="Изображение към опция", help_text="Изображение, което се показва с тази опция.")
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
    user = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name="Потребител", help_text="Потребителят, който е отговорил.")
    question = models.ForeignKey(PollQuestion, on_delete=models.CASCADE, verbose_name="Въпрос", help_text="Въпросът, на който е отговорено.")
    selected_option = models.ForeignKey(PollOption, on_delete=models.CASCADE, verbose_name="Избрана опция", help_text="Опцията, която потребителят е избрал.")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Отговорено на", help_text="Дата и час на отговаряне. Формат: YYYY-MM-DD HH:MM:SS.")

    def __str__(self):
        return f"{self.user.username} answered {self.question.title}"

    class Meta:
        verbose_name = "Отговор на анкета"
        verbose_name_plural = "Отговори на анкети"
        ordering = ['-created_at']


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
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=False, verbose_name="Автор", help_text="Авторът, който е създал/редактирал условията.")
    content = models.TextField(null=False, help_text="Пълният текст на Условията за ползване. Поддържа се Markdown.")
    date = models.DateTimeField(auto_now_add=True, help_text="Дата на последната промяна. Формат: YYYY-MM-DD HH:MM:SS.")

    def __str__(self):
        return f"{self.content} качено на : {self.user.username}"

    class Meta:
        verbose_name = "Условие за ползване"
        verbose_name_plural = "Условия за ползване"

class PrivacyPolicy(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=False, verbose_name="Автор", help_text="Авторът, който е създал/редактирал политиката.")
    content = models.TextField(null=False, help_text="Пълният текст на Политиката за поверителност. Поддържа се Markdown.")
    date = models.DateTimeField(auto_now_add=True, help_text="Дата на последната промяна. Формат: YYYY-MM-DD HH:MM:SS.")

    def __str__(self):
        return f"{self.content} качено на : {self.user.username}"

    class Meta:
        verbose_name = "Политика за поверителност"
        verbose_name_plural = "Политики за поверителност"

class PostImage(models.Model):
    post = models.ForeignKey(Posts, related_name='images', on_delete=models.CASCADE, help_text="Публикацията, към която е свързана тази снимка.")
    image = models.ImageField(upload_to=post_gallery_upload_path, help_text="Файлът с изображението.")

    def __str__(self):
        return f"Image for post: {self.post.title}"

    class Meta:
        verbose_name = "Снимка към публикация"
        verbose_name_plural = "Снимки към публикации"

class PostDocument(models.Model):
    post = models.ForeignKey(Posts, related_name='documents', on_delete=models.CASCADE, help_text="Публикацията, към която е свързан този документ.")
    file = models.FileField(upload_to=post_document_upload_path, help_text="Файлът с документа (PDF, DOCX, DOC, XLSX, XLS, PPTX, PPT, ZIP, RAR, 7Z, CSV, TXT, JSON, RTF).")
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

    class Meta:
        verbose_name = "Събитие"
        verbose_name_plural = "Събития"
        ordering = ['start_datetime']


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
    maintenance_mode = models.BooleanField(default=False, verbose_name="Режим на поддръжка", help_text="Ако е активиран, сайтът ще показва страница за поддръжка на всички потребители, които не са администратори.")
    enable_bell_suggestions = models.BooleanField(default=True, verbose_name="Активирани 'Предложения за песни'", help_text="Позволява на потребителите да предлагат песни за училищния звънец.")
    enable_weekly_poll = models.BooleanField(default=True, verbose_name="Активирана 'Седмична анкета'", help_text="Активира/деактивира показването на седмичната анкета на сайта.")
    enable_meme_of_the_week = models.BooleanField(default=True, verbose_name="Активирано 'Меме на седмицата'", help_text="Позволява на потребителите да качват и гласуват за мемета.")
    enable_user_registration = models.BooleanField(default=True, verbose_name="Активирани 'Регистрации на потребители'", help_text="Позволява на нови потребители да се регистрират в системата.")
    enable_program_page = models.BooleanField(default=True, verbose_name="Активирана страница 'Програма'", help_text="Показва или скрива страницата с учебната програма.")

    class Meta:
        verbose_name = "Настройки на сайта"
        verbose_name_plural = "Настройки на сайта"

    def save(self, *args, **kwargs):
        if not self.pk and SiteSettings.objects.exists():
            raise ValidationError('Може да съществува само един обект с настройки на сайта.')
        return super(SiteSettings, self).save(*args, **kwargs)


