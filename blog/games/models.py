import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from .definitions import GAME_CHOICES, PACK_VERSION


class GameSettings(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    enabled = models.BooleanField(default=False, verbose_name='Игри включени')
    start_date = models.DateField(null=True, blank=True, verbose_name='Начална дата')
    daily_points_cap = models.PositiveSmallIntegerField(default=30, verbose_name='Общ дневен лимит точки')
    pack_version = models.CharField(max_length=30, default=PACK_VERSION, editable=False, verbose_name="Версия на пакета")

    class Meta:
        app_label = 'blog'
        verbose_name = 'Настройки на игрите'
        verbose_name_plural = 'Настройки на игрите'
        constraints = [models.CheckConstraint(condition=models.Q(id=1), name='game_settings_singleton')]

    def clean(self):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            old = type(self).objects.get(pk=self.pk)
            if old.start_date != self.start_date and GameSession.objects.exists():
                raise ValidationError('Началната дата не може да се променя след първата игра.')
        if self.enabled:
            from .content import validate_database
            errors = validate_database()
            if not self.start_date or errors:
                raise ValidationError('Нужни са начална дата и проверен пакет: ' + '; '.join(errors[:3]))

    def __str__(self):
        return 'Миниигри'


class GamePuzzle(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key = models.CharField(max_length=100, unique=True, verbose_name="Ключ")
    game = models.CharField(max_length=12, choices=GAME_CHOICES, verbose_name="Игра")
    pool = models.CharField(max_length=10, choices=[('daily', 'Дневна'), ('practice', 'Допълнителна'), ('generated', 'Генерирана')], verbose_name="Група")
    slot = models.PositiveIntegerField(null=True, blank=True, verbose_name="Позиция")
    version = models.CharField(max_length=30, default=PACK_VERSION, verbose_name="Версия")
    # Never serialized directly. Includes answers, cards and solver witnesses.
    payload = models.JSONField(verbose_name="Данни на задачата")
    fingerprint = models.CharField(max_length=64, unique=True, verbose_name="Отпечатък")

    class Meta:
        app_label = 'blog'
        verbose_name = 'Игрова задача'
        verbose_name_plural = 'Игрови задачи'
        constraints = [models.UniqueConstraint(fields=('version', 'game', 'pool', 'slot'), name='game_puzzle_slot')]
        ordering = ('game', 'pool', 'slot')

    def __str__(self):
        return self.key


class GameSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='game_sessions', verbose_name="Потребител")
    puzzle = models.ForeignKey(GamePuzzle, on_delete=models.PROTECT, related_name='sessions', verbose_name="Задача")
    mode = models.CharField(max_length=10, choices=[('daily', 'Дневна'), ('practice', 'Допълнителна'), ('replay', 'Упражнение')], verbose_name="Режим")
    day = models.DateField(verbose_name="Ден")
    status = models.CharField(max_length=10, default='active', choices=[('active', 'Играе'), ('won', 'Победа'), ('lost', 'Загуба'), ('expired', 'Изтекла')], verbose_name="Статус")
    state = models.JSONField(default=dict, verbose_name="Състояние")
    revision = models.PositiveIntegerField(default=0, verbose_name="Ревизия")
    reward_eligible = models.BooleanField(default=True, verbose_name="Носи точки")
    points_awarded = models.PositiveSmallIntegerField(default=0, verbose_name="Дадени точки")
    score = models.PositiveIntegerField(null=True, blank=True, verbose_name="Резултат")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Започната на")
    expires_at = models.DateTimeField(verbose_name="Изтича на")
    finished_at = models.DateTimeField(null=True, blank=True, verbose_name="Завършена на")

    class Meta:
        app_label = 'blog'
        verbose_name = 'Игрова сесия'
        verbose_name_plural = 'Игрови сесии'
        indexes = [models.Index(fields=('user', 'day', 'status'), name='game_user_day'), models.Index(fields=('user', 'puzzle'), name='game_user_puzzle')]


class GameRequest(models.Model):
    """Idempotency receipts for both session starts and moves."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    key = models.UUIDField()
    session = models.ForeignKey(GameSession, on_delete=models.CASCADE)
    fingerprint = models.CharField(max_length=64)

    class Meta:
        app_label = 'blog'
        constraints = [models.UniqueConstraint(fields=('user', 'key'), name='game_request_once')]
