import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('blog', '0071_team_chat'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='teamchatmessage',
            name='is_pinned',
            field=models.BooleanField(
                db_index=True,
                default=False,
                verbose_name='Закачено',
            ),
        ),
        migrations.AddField(
            model_name='teamchatmessage',
            name='pinned_at',
            field=models.DateTimeField(
                blank=True,
                null=True,
                verbose_name='Закачено на',
            ),
        ),
        migrations.AddField(
            model_name='teamchatmessage',
            name='pinned_by',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='pinned_team_chat_messages',
                to=settings.AUTH_USER_MODEL,
                verbose_name='Закачено от',
            ),
        ),
        migrations.AlterField(
            model_name='teamchatmessagerevision',
            name='event',
            field=models.CharField(
                choices=[
                    ('edited', 'Редактирано'),
                    ('deleted', 'Изтрито'),
                    ('pinned', 'Закачено'),
                    ('unpinned', 'Откачено'),
                ],
                max_length=12,
                verbose_name='Действие',
            ),
        ),
        migrations.CreateModel(
            name='TeamChatReaction',
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
                    'emoji',
                    models.CharField(
                        choices=[
                            ('👍', 'Харесвам'),
                            ('✅', 'Готово'),
                            ('👀', 'Прегледано'),
                            ('❗', 'Важно'),
                        ],
                        max_length=8,
                        verbose_name='Реакция',
                    ),
                ),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                (
                    'message',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='reactions',
                        to='blog.teamchatmessage',
                        verbose_name='Съобщение',
                    ),
                ),
                (
                    'user',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='team_chat_reactions',
                        to=settings.AUTH_USER_MODEL,
                        verbose_name='Потребител',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Реакция в екипния чат',
                'verbose_name_plural': 'Реакции в екипния чат',
                'default_permissions': (),
                'constraints': [
                    models.UniqueConstraint(
                        fields=('message', 'user', 'emoji'),
                        name='unique_team_chat_reaction',
                    ),
                ],
            },
        ),
    ]
