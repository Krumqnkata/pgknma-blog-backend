import secrets
import string
import uuid
from datetime import datetime, time, timedelta

from django.core.exceptions import ValidationError
from django.core.validators import MaxLengthValidator
from django.db import models
from django.utils import timezone


ACCESS_CODE_ALPHABET = string.ascii_uppercase + string.digits


def generate_unique_code():
    """Generate a human-friendly, cryptographically secure access code."""
    while True:
        code = "".join(secrets.choice(ACCESS_CODE_ALPHABET) for _ in range(6))
        if not Profile.objects.filter(code=code).exists():
            return code


def default_session_expiry():
    return timezone.now() + timedelta(days=14)


class Profile(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=6, unique=True, editable=False)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    clas = models.CharField(max_length=4)
    email = models.EmailField()

    @property
    def clas_bg(self):
        mapping = {"A": "А", "B": "Б", "V": "В", "G": "Г"}
        if self.clas == "U":
            return "Учител"
        if not self.clas or len(self.clas) < 2:
            return self.clas
        grade = self.clas[:-1]
        letter = self.clas[-1].upper()
        return f"{grade}{mapping.get(letter, letter)}"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    def save(self, *args, **kwargs):
        cyrillic_to_latin = {"А": "A", "Б": "B", "В": "V", "Г": "G"}
        if self.clas:
            self.clas = self.clas.strip().upper()
            last_char = self.clas[-1]
            if last_char in cyrillic_to_latin:
                self.clas = self.clas[:-1] + cyrillic_to_latin[last_char]
        if not self.code:
            self.code = generate_unique_code()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.full_name} ({self.clas_bg})"

    class Meta:
        ordering = ("clas", "first_name", "last_name")
        verbose_name = "Valentine профил"
        verbose_name_plural = "Valentine профили"


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
        return not self.revoked and self.expires_at > timezone.now()

    def __str__(self):
        return f"Сесия на {self.profile.full_name}"

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "Valentine сесия"
        verbose_name_plural = "Valentine сесии"


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
        verbose_name = "Архивирана валентинка"
        verbose_name_plural = "Архивирани валентинки"


class Settings(models.Model):
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
        if not self.pk and Settings.objects.exists():
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
        year = self.get_reveal_at().year
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
