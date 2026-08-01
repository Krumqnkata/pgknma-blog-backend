from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('blog', '0050_blogloginsession'),
    ]

    operations = [
        migrations.CreateModel(
            name='PersonalDataCleanupRun',
            fields=[
                (
                    'id',
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name='ID',
                    ),
                ),
                (
                    'started_at',
                    models.DateTimeField(
                        auto_now_add=True,
                        verbose_name='Стартирано на',
                    ),
                ),
                (
                    'finished_at',
                    models.DateTimeField(
                        blank=True,
                        null=True,
                        verbose_name='Завършено на',
                    ),
                ),
                (
                    'retention_days',
                    models.PositiveIntegerField(verbose_name='Срок в дни'),
                ),
                (
                    'cutoff_at',
                    models.DateTimeField(
                        verbose_name='Изтриване на записи преди',
                    ),
                ),
                (
                    'dry_run',
                    models.BooleanField(
                        default=False,
                        verbose_name='Само проверка',
                    ),
                ),
                (
                    'status',
                    models.CharField(
                        choices=[
                            ('running', 'Изпълнява се'),
                            ('succeeded', 'Успешно'),
                            ('failed', 'Грешка'),
                        ],
                        default='running',
                        max_length=12,
                        verbose_name='Статус',
                    ),
                ),
                (
                    'result_counts',
                    models.JSONField(
                        blank=True,
                        default=dict,
                        verbose_name='Резултати по категории',
                    ),
                ),
                (
                    'error_message',
                    models.TextField(
                        blank=True,
                        default='',
                        verbose_name='Грешка',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Изпълнение на почистването на лични данни',
                'verbose_name_plural': 'История на почистването на лични данни',
                'ordering': ('-started_at',),
                'indexes': [
                    models.Index(
                        fields=['started_at'],
                        name='cleanup_run_started_idx',
                    ),
                    models.Index(
                        fields=['status', 'started_at'],
                        name='cleanup_run_status_idx',
                    ),
                ],
            },
        ),
    ]
