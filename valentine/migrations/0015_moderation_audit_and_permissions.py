from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def preflight_settings_singleton(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor == "mysql":
        version = connection.mysql_version
        is_mariadb = connection.mysql_is_mariadb
        minimum_version = (10, 6) if is_mariadb else (8, 0, 16)
        if version < minimum_version:
            database_name = "MariaDB" if is_mariadb else "MySQL"
            required = ".".join(str(part) for part in minimum_version)
            detected = ".".join(str(part) for part in version)
            raise RuntimeError(
                "Migration 0015 requires "
                f"{database_name} >= {required} so its CHECK constraint "
                "is enforced. "
                f"Detected {database_name} {detected}; no DDL was run."
            )

    Settings = apps.get_model("valentine", "Settings")
    settings_count = Settings.objects.count()
    if settings_count > 1:
        raise RuntimeError(
            "Открити са повече от едни Valentine настройки. "
            "Миграцията не обединява и не изтрива конфигурация автоматично; "
            "прегледай записите ръчно и остави точно един."
        )


def populate_settings_singleton_key(apps, schema_editor):
    # Check again after the nullable column has been added. The first preflight
    # runs before any DDL so MySQL cannot be left with an unrecorded partial
    # migration; this second guard also covers a concurrent settings insert.
    preflight_settings_singleton(apps, schema_editor)
    Settings = apps.get_model("valentine", "Settings")
    Settings.objects.update(singleton_key="default")


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("valentine", "0014_alter_profile_email"),
    ]

    operations = [
        # This must be the first operation: MySQL cannot roll back DDL, so
        # legacy conflicts have to stop the migration before AddField runs.
        migrations.RunPython(
            preflight_settings_singleton,
            migrations.RunPython.noop,
        ),
        migrations.AlterModelOptions(
            name="archivedvalentine",
            options={
                "proxy": True,
                "verbose_name": "Валентинка от минала кампания",
                "verbose_name_plural": "Валентинки от минали кампании",
            },
        ),
        migrations.AlterModelOptions(
            name="profile",
            options={
                "ordering": ("clas", "first_name", "last_name"),
                "permissions": (
                    (
                        "view_profile_codes",
                        "Може да вижда и експортира Valentine кодове",
                    ),
                ),
                "verbose_name": "Valentine профил",
                "verbose_name_plural": "Valentine профили",
            },
        ),
        migrations.AddField(
            model_name="settings",
            name="singleton_key",
            field=models.CharField(
                editable=False,
                max_length=20,
                null=True,
            ),
        ),
        migrations.RunPython(
            populate_settings_singleton_key,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="settings",
            name="singleton_key",
            field=models.CharField(
                default="default",
                editable=False,
                max_length=20,
                unique=True,
            ),
        ),
        migrations.AddConstraint(
            model_name="settings",
            constraint=models.CheckConstraint(
                condition=models.Q(singleton_key="default"),
                name="valentine_settings_singleton_key",
            ),
        ),
        migrations.CreateModel(
            name="ValentineLoginAttempt",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "client_key",
                    models.CharField(
                        max_length=24,
                        unique=True,
                        verbose_name="Хеш на клиент",
                    ),
                ),
                (
                    "attempts",
                    models.PositiveIntegerField(
                        default=0,
                        verbose_name="Неуспешни опити",
                    ),
                ),
                (
                    "window_expires",
                    models.DateTimeField(
                        db_index=True,
                        verbose_name="Край на прозореца",
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(
                        auto_now=True,
                        verbose_name="Обновено на",
                    ),
                ),
            ],
            options={
                "verbose_name": "Ограничение за Valentine вход",
                "verbose_name_plural": "Ограничения за Valentine вход",
                "ordering": ("-updated_at",),
            },
        ),
        migrations.CreateModel(
            name="ValentineModerationEvent",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "dispatch_id",
                    models.UUIDField(db_index=True, verbose_name="Пратка"),
                ),
                (
                    "event_type",
                    models.CharField(
                        choices=[
                            ("reported", "Докладвано от получател"),
                            ("hidden", "Скрито от модератор"),
                            ("restored", "Възстановено от модератор"),
                        ],
                        max_length=20,
                        verbose_name="Действие",
                    ),
                ),
                (
                    "reason",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("offensive", "Обидно съдържание"),
                            ("harassment", "Тормоз"),
                            ("inappropriate", "Неподходящо съдържание"),
                            ("spam", "Спам"),
                            ("other", "Друга причина"),
                        ],
                        max_length=20,
                        verbose_name="Причина",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True,
                        verbose_name="Дата",
                    ),
                ),
                (
                    "campaign",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="moderation_events",
                        to="valentine.valentinecampaign",
                        verbose_name="Кампания",
                    ),
                ),
                (
                    "moderator",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="valentine_moderation_events",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Модератор",
                    ),
                ),
                (
                    "reporter_profile",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="submitted_valentine_reports",
                        to="valentine.profile",
                        verbose_name="Докладвал получател",
                    ),
                ),
            ],
            options={
                "verbose_name": "Събитие по модерация",
                "verbose_name_plural": "История на модерацията",
                "ordering": ("-created_at",),
                "constraints": [
                    models.UniqueConstraint(
                        fields=(
                            "campaign",
                            "dispatch_id",
                            "reporter_profile",
                            "event_type",
                        ),
                        name="unique_recipient_report_per_dispatch",
                    ),
                ],
            },
        ),
    ]
