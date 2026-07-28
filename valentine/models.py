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


class Valenitines(models.Model):
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
        on_delete=models.CASCADE,
        related_name="received_valentines",
        verbose_name="Получател",
    )
    sender_profile = models.ForeignKey(
        Profile,
        on_delete=models.SET_NULL,
        related_name="sent_valentines",
        null=True,
        blank=True,
        verbose_name="Изпращач",
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

    def __str__(self):
        return f"До {self.profile.full_name} от {self.sender_profile or 'системата'}"

    class Meta:
        ordering = ("-date",)
        verbose_name = "Валентинка"
        verbose_name_plural = "Валентинки"


class Settings(models.Model):
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
    show_received_count = models.BooleanField(
        default=True,
        verbose_name="Показвай броя получени преди разкриването",
    )

    def clean(self):
        if (
            self.sending_starts_at
            and self.sending_ends_at
            and self.sending_starts_at >= self.sending_ends_at
        ):
            raise ValidationError(
                "Краят на изпращането трябва да бъде след началото."
            )
        if self.daily_limit < 1:
            raise ValidationError("Дневният лимит трябва да бъде поне 1.")

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

    def sending_is_open(self, at=None):
        at = at or timezone.now()
        if not self.valentines_enabled:
            return False
        if self.sending_starts_at and at < self.sending_starts_at:
            return False
        if self.sending_ends_at and at > self.sending_ends_at:
            return False
        return True

    def __str__(self):
        return "Настройки на Valentine платформата"

    class Meta:
        verbose_name = "Настройки"
        verbose_name_plural = "Настройки"
