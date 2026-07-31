import secrets
import string
import unicodedata
import uuid
from datetime import datetime, time, timedelta

from django.conf import settings as django_settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.core.validators import MaxLengthValidator
from django.db import models
from django.utils import timezone


ACCESS_CODE_ALPHABET = string.ascii_uppercase + string.digits
CLASS_CODE_TRANSLATION = str.maketrans(
    {
        "А": "A",
        "Б": "B",
        "В": "V",
        "Г": "G",
        "У": "U",
        "\u200b": None,
        "\u200c": None,
        "\u200d": None,
        "\ufeff": None,
    }
)
VALID_STUDENT_CLASSES = {
    f"{grade}{letter}"
    for grade in range(1, 13)
    for letter in "ABVG"
}


def normalize_class_code(value):
    """Return one canonical class code, including for legacy imports."""
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKC", str(value))
    normalized = normalized.upper().translate(CLASS_CODE_TRANSLATION)
    return "".join(normalized.split())


def validate_class_code(value):
    """Accept teachers or Bulgarian school classes from 1A through 12G."""
    normalized = normalize_class_code(value)
    if normalized != "U" and normalized not in VALID_STUDENT_CLASSES:
        raise ValidationError(
            "Класът трябва да е между 1А и 12Г или „U“ за учител."
        )


def generate_unique_code():
    """Generate a human-friendly, cryptographically secure access code."""
    while True:
        code = "".join(secrets.choice(ACCESS_CODE_ALPHABET) for _ in range(6))
        if not Profile.objects.filter(code=code).exists():
            return code


def default_session_expiry():
    return timezone.now() + timedelta(days=14)


def default_academic_year_start():
    today = timezone.localdate()
    return today.year if today.month >= 9 else today.year - 1


class Profile(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=6, unique=True, editable=False)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    clas = models.CharField(max_length=4)
    email = models.EmailField(blank=True, verbose_name="Имейл")
    is_active = models.BooleanField(
        default=True,
        verbose_name="Активен профил",
        help_text=(
            "Неактивният профил не може да влиза или да получава нови "
            "валентинки, но историята му се запазва."
        ),
    )
    academic_year_start = models.PositiveSmallIntegerField(
        default=default_academic_year_start,
        verbose_name="Начало на учебната година",
        help_text=(
            "Например 2025 означава учебната 2025/2026 година."
        ),
    )
    pin_hash = models.CharField(
        max_length=256,
        blank=True,
        editable=False,
        verbose_name="PIN хеш",
    )
    pin_set_at = models.DateTimeField(
        null=True,
        blank=True,
        editable=False,
        verbose_name="PIN зададен на",
    )
    pin_failed_attempts = models.PositiveSmallIntegerField(
        default=0,
        editable=False,
        verbose_name="Грешни PIN опити",
    )
    pin_locked_until = models.DateTimeField(
        null=True,
        blank=True,
        editable=False,
        verbose_name="PIN заключен до",
    )

    @property
    def clas_bg(self):
        class_code = normalize_class_code(self.clas)
        mapping = {"A": "А", "B": "Б", "V": "В", "G": "Г"}
        if class_code == "U":
            return "Учител"
        if not class_code or len(class_code) < 2:
            return class_code
        grade = class_code[:-1]
        letter = class_code[-1]
        return f"{grade}{mapping.get(letter, letter)}"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def academic_year_label(self):
        if normalize_class_code(self.clas) == "U":
            return "—"
        return (
            f"{self.academic_year_start}/"
            f"{self.academic_year_start + 1}"
        )

    @property
    def has_pin(self):
        return bool(self.pin_hash)

    def set_pin(self, pin):
        """Store a PIN safely; its plaintext value is never persisted."""
        self.pin_hash = make_password(pin)
        self.pin_set_at = timezone.now()
        self.pin_failed_attempts = 0
        self.pin_locked_until = None

    def check_pin(self, pin):
        return bool(self.pin_hash) and check_password(pin, self.pin_hash)

    def reset_pin(self):
        self.pin_hash = ""
        self.pin_set_at = None
        self.pin_failed_attempts = 0
        self.pin_locked_until = None

    def clean_fields(self, exclude=None):
        # ModelForm runs field validation before ``clean()``, so normalize here.
        self.clas = normalize_class_code(self.clas)
        super().clean_fields(exclude=exclude)
        if not exclude or "clas" not in exclude:
            validate_class_code(self.clas)

    def save(self, *args, **kwargs):
        self.clas = normalize_class_code(self.clas)
        if not self.code:
            self.code = generate_unique_code()
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.full_name} ({self.clas_bg})"

    class Meta:
        ordering = ("clas", "first_name", "last_name")
        verbose_name = "Valentine профил"
        verbose_name_plural = "Valentine профили"
        permissions = (
            (
                "view_profile_codes",
                "Може да вижда и експортира Valentine кодове",
            ),
        )


class Register(models.Model):
    uuid = models.UUIDField(
        primary_key=True,
        editable=False,
        default=uuid.uuid4,
        unique=True,
    )
    profile = models.ForeignKey(
        Profile,
        on_delete=models.CASCADE,
        related_name="sessions",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(default=default_session_expiry)
    revoked = models.BooleanField(default=False)

    @property
    def is_valid(self):
        return (
            self.profile.is_active
            and not self.revoked
            and self.expires_at > timezone.now()
        )

    def __str__(self):
        return f"Сесия на {self.profile.full_name}"

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "Valentine сесия"
        verbose_name_plural = "Valentine сесии"


class ValentineLoginAttempt(models.Model):
    client_key = models.CharField(
        max_length=24,
        unique=True,
        verbose_name="Хеш на клиент",
    )
    attempts = models.PositiveIntegerField(
        default=0,
        verbose_name="Неуспешни опити",
    )
    window_expires = models.DateTimeField(
        db_index=True,
        verbose_name="Край на прозореца",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="Обновено на",
    )

    def __str__(self):
        return f"{self.client_key} · {self.attempts} опита"

    class Meta:
        ordering = ("-updated_at",)
        verbose_name = "Ограничение за Valentine вход"
        verbose_name_plural = "Ограничения за Valentine вход"


class ValentineCampaign(models.Model):
    year = models.PositiveSmallIntegerField(
        unique=True,
        verbose_name="Година",
    )
    name = models.CharField(
        max_length=120,
        blank=True,
        verbose_name="Име на кампанията",
    )
    is_archived = models.BooleanField(
        default=False,
        verbose_name="Архивирана",
    )
    archived_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Архивирана на",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Създадена на",
    )

    def save(self, *args, **kwargs):
        if not self.name:
            self.name = f"Valentine {self.year}"
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name or f"Valentine {self.year}"

    class Meta:
        ordering = ("-year",)
        verbose_name = "Valentine кампания"
        verbose_name_plural = "Valentine кампании"


class Valenitines(models.Model):
    class AudienceType(models.TextChoices):
        INDIVIDUAL = "individual", "Един човек"
        CLASS = "class", "Цял клас"
        TEACHERS = "teachers", "Всички учители"

    class Theme(models.TextChoices):
        CLASSIC = "classic", "Класическа"
        SUNSET = "sunset", "Залез"
        MIDNIGHT = "midnight", "Среднощна"
        CANDY = "candy", "Бонбонена"
        BLOOM = "bloom", "Цветна"

    class ReportReason(models.TextChoices):
        OFFENSIVE = "offensive", "Обидно съдържание"
        HARASSMENT = "harassment", "Тормоз"
        INAPPROPRIATE = "inappropriate", "Неподходящо съдържание"
        SPAM = "spam", "Спам"
        OTHER = "other", "Друга причина"

    class ModerationStatus(models.TextChoices):
        CLEAR = "clear", "Без сигнал"
        PENDING = "pending", "Чака преглед"
        HIDDEN = "hidden", "Скрито от модератор"
        RESTORED = "restored", "Възстановено"

    profile = models.ForeignKey(
        Profile,
        on_delete=models.PROTECT,
        related_name="received_valentines",
        verbose_name="Получател",
    )
    sender_profile = models.ForeignKey(
        Profile,
        on_delete=models.PROTECT,
        related_name="sent_valentines",
        null=True,
        blank=True,
        verbose_name="Изпращач",
    )
    campaign = models.ForeignKey(
        ValentineCampaign,
        on_delete=models.PROTECT,
        related_name="valentines",
        verbose_name="Кампания",
    )
    recipient_class_snapshot = models.CharField(
        max_length=4,
        blank=True,
        verbose_name="Клас на получателя при изпращане",
    )
    sender_class_snapshot = models.CharField(
        max_length=4,
        blank=True,
        verbose_name="Клас на изпращача при изпращане",
    )
    dispatch_id = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        db_index=True,
        verbose_name="Пратка",
    )
    audience_type = models.CharField(
        max_length=20,
        choices=AudienceType.choices,
        default=AudienceType.INDIVIDUAL,
        verbose_name="Тип получател",
    )
    audience_label = models.CharField(
        max_length=120,
        blank=True,
        verbose_name="Получатели",
    )
    content = models.TextField(
        validators=[MaxLengthValidator(500)],
        verbose_name="Послание",
    )
    theme = models.CharField(
        max_length=20,
        choices=Theme.choices,
        default=Theme.CLASSIC,
        verbose_name="Тема",
    )
    is_anonymous = models.BooleanField(default=True, verbose_name="Анонимно")
    is_reported = models.BooleanField(default=False, verbose_name="Докладвано")
    report_reason = models.CharField(
        max_length=20,
        choices=ReportReason.choices,
        blank=True,
        verbose_name="Причина за сигнала",
    )
    reported_at = models.DateTimeField(null=True, blank=True)
    moderation_status = models.CharField(
        max_length=20,
        choices=ModerationStatus.choices,
        default=ModerationStatus.CLEAR,
        verbose_name="Модерация",
    )
    date = models.DateTimeField(auto_now_add=True)
    edited_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Последна редакция",
    )

    def save(self, *args, **kwargs):
        if not self.recipient_class_snapshot and self.profile_id:
            self.recipient_class_snapshot = self.profile.clas
        self.recipient_class_snapshot = normalize_class_code(
            self.recipient_class_snapshot
        )
        if (
            not self.sender_class_snapshot
            and self.sender_profile_id
        ):
            self.sender_class_snapshot = self.sender_profile.clas
        self.sender_class_snapshot = normalize_class_code(
            self.sender_class_snapshot
        )
        super().save(*args, **kwargs)

    def __str__(self):
        return f"До {self.profile.full_name} от {self.sender_profile or 'системата'}"

    class Meta:
        ordering = ("-date",)
        verbose_name = "Валентинка"
        verbose_name_plural = "Валентинки"
        indexes = (
            models.Index(
                fields=("sender_profile", "date"),
                name="val_sender_date_idx",
            ),
            models.Index(
                fields=("sender_profile", "dispatch_id"),
                name="val_sender_dispatch_idx",
            ),
            models.Index(
                fields=("campaign", "profile", "date"),
                name="val_campaign_recv_idx",
            ),
            models.Index(
                fields=("campaign", "sender_profile", "date"),
                name="val_campaign_sent_idx",
            ),
        )


class ArchivedValentine(Valenitines):
    class Meta:
        proxy = True
        verbose_name = "Валентинка от минала кампания"
        verbose_name_plural = "Валентинки от минали кампании"


class ValentineModerationEvent(models.Model):
    class EventType(models.TextChoices):
        REPORTED = "reported", "Докладвано от получател"
        HIDDEN = "hidden", "Скрито от модератор"
        RESTORED = "restored", "Възстановено от модератор"

    campaign = models.ForeignKey(
        ValentineCampaign,
        on_delete=models.PROTECT,
        related_name="moderation_events",
        verbose_name="Кампания",
    )
    dispatch_id = models.UUIDField(
        db_index=True,
        verbose_name="Пратка",
    )
    event_type = models.CharField(
        max_length=20,
        choices=EventType.choices,
        verbose_name="Действие",
    )
    reporter_profile = models.ForeignKey(
        Profile,
        on_delete=models.PROTECT,
        related_name="submitted_valentine_reports",
        null=True,
        blank=True,
        verbose_name="Докладвал получател",
    )
    moderator = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="valentine_moderation_events",
        null=True,
        blank=True,
        verbose_name="Модератор",
    )
    reason = models.CharField(
        max_length=20,
        choices=Valenitines.ReportReason.choices,
        blank=True,
        verbose_name="Причина",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Дата",
    )

    def __str__(self):
        return (
            f"{self.get_event_type_display()} · "
            f"{self.campaign} · {self.dispatch_id}"
        )

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "Събитие по модерация"
        verbose_name_plural = "История на модерацията"
        constraints = (
            models.UniqueConstraint(
                fields=(
                    "campaign",
                    "dispatch_id",
                    "reporter_profile",
                    "event_type",
                ),
                name="unique_recipient_report_per_dispatch",
            ),
        )


class Settings(models.Model):
    singleton_key = models.CharField(
        max_length=20,
        default="default",
        unique=True,
        editable=False,
    )
    active_campaign = models.ForeignKey(
        ValentineCampaign,
        on_delete=models.PROTECT,
        related_name="+",
        null=True,
        blank=True,
        verbose_name="Активна кампания",
    )
    reveal_date = models.DateField(verbose_name="Резервна дата за разкриване")
    reveal_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Точен момент за разкриване",
        help_text="Ако е празно, се използва началото на резервната дата.",
    )
    sending_starts_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Начало на изпращането",
    )
    sending_ends_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Край на изпращането",
    )
    valentines_enabled = models.BooleanField(
        default=True,
        verbose_name="Разрешено изпращане",
    )
    daily_limit = models.PositiveSmallIntegerField(
        default=10,
        verbose_name="Дневен лимит на човек",
    )
    cooldown_seconds = models.PositiveSmallIntegerField(
        default=30,
        verbose_name="Пауза между две изпращания (секунди)",
    )
    group_daily_limit = models.PositiveSmallIntegerField(
        default=2,
        verbose_name="Дневен лимит за групови пратки",
        help_text=(
            "Групова пратка до клас или всички учители се брои като една."
        ),
    )
    show_received_count = models.BooleanField(
        default=True,
        verbose_name="Показвай броя получени преди разкриването",
    )

    def clean(self):
        if self.active_campaign_id and self.active_campaign.is_archived:
            raise ValidationError(
                "Архивирана кампания не може да бъде активна."
            )
        if self.active_campaign_id:
            effective_reveal_at = self.get_reveal_at()
            if timezone.is_naive(effective_reveal_at):
                effective_reveal_at = timezone.make_aware(
                    effective_reveal_at,
                    timezone.get_current_timezone(),
                )
            if (
                timezone.localtime(effective_reveal_at).year
                != self.active_campaign.year
            ):
                raise ValidationError(
                    "Годината на разкриване трябва да съвпада с "
                    "годината на активната кампания."
                )
        if (
            self.sending_starts_at
            and self.sending_ends_at
            and self.sending_starts_at >= self.sending_ends_at
        ):
            raise ValidationError(
                "Краят на изпращането трябва да бъде след началото."
            )
        reveal_at = (
            self.get_reveal_at()
            if self.reveal_at or self.reveal_date
            else None
        )
        if (
            self.sending_ends_at
            and reveal_at
            and reveal_at < self.sending_ends_at
        ):
            raise ValidationError(
                "Разкриването трябва да бъде след края на изпращането."
            )
        if (
            self.sending_starts_at
            and reveal_at
            and reveal_at <= self.sending_starts_at
        ):
            raise ValidationError(
                "Разкриването трябва да бъде след началото на изпращането."
            )
        if self.daily_limit < 1:
            raise ValidationError("Дневният лимит трябва да бъде поне 1.")
        if self.group_daily_limit < 1:
            raise ValidationError(
                "Дневният лимит за групови пратки трябва да бъде поне 1."
            )

    def save(self, *args, **kwargs):
        self.singleton_key = "default"
        if (
            not self.pk
            and Settings.objects.filter(singleton_key=self.singleton_key)
            .exists()
        ):
            raise ValidationError("Може да има само един Valentine конфигурационен запис.")
        self.full_clean()
        return super().save(*args, **kwargs)

    def get_reveal_at(self):
        if self.reveal_at:
            return self.reveal_at
        value = datetime.combine(self.reveal_date, time.min)
        return timezone.make_aware(value, timezone.get_current_timezone())

    def ensure_active_campaign(self):
        if self.active_campaign_id:
            return self.active_campaign
        reveal_at = self.get_reveal_at()
        if timezone.is_naive(reveal_at):
            reveal_at = timezone.make_aware(
                reveal_at,
                timezone.get_current_timezone(),
            )
        year = timezone.localtime(reveal_at).year
        campaign, _ = ValentineCampaign.objects.get_or_create(
            year=year,
            defaults={"name": f"Valentine {year}"},
        )
        if campaign.is_archived:
            campaign.is_archived = False
            campaign.archived_at = None
            campaign.save(update_fields=["is_archived", "archived_at"])
        self.active_campaign = campaign
        if self.pk:
            Settings.objects.filter(pk=self.pk).update(
                active_campaign=campaign
            )
        return campaign

    def sending_is_open(self, at=None):
        at = at or timezone.now()
        if not self.valentines_enabled:
            return False
        if self.active_campaign_id and self.active_campaign.is_archived:
            return False
        if self.sending_starts_at and at < self.sending_starts_at:
            return False
        if self.sending_ends_at and at > self.sending_ends_at:
            return False
        if at >= self.get_reveal_at():
            return False
        return True

    def __str__(self):
        return "Настройки на Valentine платформата"

    class Meta:
        verbose_name = "Настройки"
        verbose_name_plural = "Настройки"
        constraints = (
            models.CheckConstraint(
                condition=models.Q(singleton_key="default"),
                name="valentine_settings_singleton_key",
            ),
        )
