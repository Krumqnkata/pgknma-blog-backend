import django.core.validators
import django.db.models.deletion
import django.utils.timezone
import valentine.models
from django.db import migrations, models


def mark_existing_reports_pending(apps, schema_editor):
    Valentine = apps.get_model("valentine", "Valenitines")
    Valentine.objects.filter(is_reported=True).update(
        moderation_status="pending"
    )


class Migration(migrations.Migration):
    dependencies = [
        (
            "valentine",
            "0007_valenitines_is_anonymous_valenitines_is_reported_and_more",
        ),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="profile",
            options={
                "ordering": ("clas", "first_name", "last_name"),
                "verbose_name": "Valentine профил",
                "verbose_name_plural": "Valentine профили",
            },
        ),
        migrations.AlterModelOptions(
            name="register",
            options={
                "ordering": ("-created_at",),
                "verbose_name": "Valentine сесия",
                "verbose_name_plural": "Valentine сесии",
            },
        ),
        migrations.AlterModelOptions(
            name="valenitines",
            options={
                "ordering": ("-date",),
                "verbose_name": "Валентинка",
                "verbose_name_plural": "Валентинки",
            },
        ),
        migrations.AddField(
            model_name="register",
            name="created_at",
            field=models.DateTimeField(
                auto_now_add=True,
                default=django.utils.timezone.now,
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="register",
            name="expires_at",
            field=models.DateTimeField(
                default=valentine.models.default_session_expiry
            ),
        ),
        migrations.AddField(
            model_name="register",
            name="revoked",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="settings",
            name="cooldown_seconds",
            field=models.PositiveSmallIntegerField(
                default=30,
                verbose_name="Пауза между две изпращания (секунди)",
            ),
        ),
        migrations.AddField(
            model_name="settings",
            name="daily_limit",
            field=models.PositiveSmallIntegerField(
                default=10,
                verbose_name="Дневен лимит на човек",
            ),
        ),
        migrations.AddField(
            model_name="settings",
            name="reveal_at",
            field=models.DateTimeField(
                blank=True,
                help_text="Ако е празно, се използва началото на резервната дата.",
                null=True,
                verbose_name="Точен момент за разкриване",
            ),
        ),
        migrations.AddField(
            model_name="settings",
            name="sending_ends_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                verbose_name="Край на изпращането",
            ),
        ),
        migrations.AddField(
            model_name="settings",
            name="sending_starts_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                verbose_name="Начало на изпращането",
            ),
        ),
        migrations.AddField(
            model_name="settings",
            name="show_received_count",
            field=models.BooleanField(
                default=True,
                verbose_name="Показвай броя получени преди разкриването",
            ),
        ),
        migrations.AddField(
            model_name="valenitines",
            name="moderation_status",
            field=models.CharField(
                choices=[
                    ("clear", "Без сигнал"),
                    ("pending", "Чака преглед"),
                    ("hidden", "Скрито от модератор"),
                    ("restored", "Възстановено"),
                ],
                default="clear",
                max_length=20,
                verbose_name="Модерация",
            ),
        ),
        migrations.AddField(
            model_name="valenitines",
            name="report_reason",
            field=models.CharField(
                blank=True,
                choices=[
                    ("offensive", "Обидно съдържание"),
                    ("harassment", "Тормоз"),
                    ("inappropriate", "Неподходящо съдържание"),
                    ("spam", "Спам"),
                    ("other", "Друга причина"),
                ],
                max_length=20,
                verbose_name="Причина за сигнала",
            ),
        ),
        migrations.AddField(
            model_name="valenitines",
            name="reported_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="valenitines",
            name="theme",
            field=models.CharField(
                choices=[
                    ("classic", "Класическа"),
                    ("sunset", "Залез"),
                    ("midnight", "Среднощна"),
                    ("candy", "Бонбонена"),
                    ("bloom", "Цветна"),
                ],
                default="classic",
                max_length=20,
                verbose_name="Тема",
            ),
        ),
        migrations.AlterField(
            model_name="register",
            name="profile",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="sessions",
                to="valentine.profile",
            ),
        ),
        migrations.AlterField(
            model_name="settings",
            name="reveal_date",
            field=models.DateField(
                verbose_name="Резервна дата за разкриване"
            ),
        ),
        migrations.AlterField(
            model_name="settings",
            name="valentines_enabled",
            field=models.BooleanField(
                default=True,
                verbose_name="Разрешено изпращане",
            ),
        ),
        migrations.AlterField(
            model_name="valenitines",
            name="content",
            field=models.TextField(
                validators=[django.core.validators.MaxLengthValidator(500)],
                verbose_name="Послание",
            ),
        ),
        migrations.AlterField(
            model_name="valenitines",
            name="is_anonymous",
            field=models.BooleanField(default=True, verbose_name="Анонимно"),
        ),
        migrations.AlterField(
            model_name="valenitines",
            name="is_reported",
            field=models.BooleanField(
                default=False,
                verbose_name="Докладвано",
            ),
        ),
        migrations.AlterField(
            model_name="valenitines",
            name="profile",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="received_valentines",
                to="valentine.profile",
                verbose_name="Получател",
            ),
        ),
        migrations.AlterField(
            model_name="valenitines",
            name="sender_profile",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="sent_valentines",
                to="valentine.profile",
                verbose_name="Изпращач",
            ),
        ),
        migrations.RunPython(
            mark_existing_reports_pending,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
