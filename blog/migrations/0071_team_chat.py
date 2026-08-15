from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('blog', '0070_notification_expiration'),
        ('contenttypes', '0002_remove_content_type_name'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='TeamChatMessage',
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
                    'author_name',
                    models.CharField(
                        max_length=300,
                        verbose_name='Име на автора при изпращане',
                    ),
                ),
                (
                    'text',
                    models.TextField(
                        blank=True,
                        max_length=4000,
                        verbose_name='Съобщение',
                    ),
                ),
                (
                    'created_at',
                    models.DateTimeField(
                        auto_now_add=True,
                        db_index=True,
                        verbose_name='Изпратено на',
                    ),
                ),
                (
                    'updated_at',
                    models.DateTimeField(
                        auto_now=True,
                        db_index=True,
                        verbose_name='Обновено на',
                    ),
                ),
                (
                    'edited_at',
                    models.DateTimeField(
                        blank=True,
                        null=True,
                        verbose_name='Редактирано на',
                    ),
                ),
                (
                    'deleted_at',
                    models.DateTimeField(
                        blank=True,
                        db_index=True,
                        null=True,
                        verbose_name='Изтрито на',
                    ),
                ),
                (
                    'author',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='team_chat_messages',
                        to=settings.AUTH_USER_MODEL,
                        verbose_name='Автор',
                    ),
                ),
                (
                    'deleted_by',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='deleted_team_chat_messages',
                        to=settings.AUTH_USER_MODEL,
                        verbose_name='Изтрито от',
                    ),
                ),
                (
                    'mentions',
                    models.ManyToManyField(
                        blank=True,
                        related_name='team_chat_mentions',
                        to=settings.AUTH_USER_MODEL,
                        verbose_name='Споменати потребители',
                    ),
                ),
                (
                    'reply_to',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='replies',
                        to='blog.teamchatmessage',
                        verbose_name='Отговор на',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Съобщение в екипния чат',
                'verbose_name_plural': 'Съобщения в екипния чат',
                'ordering': ('created_at', 'pk'),
                'default_permissions': (),
            },
        ),
        migrations.CreateModel(
            name='TeamChatMessageRevision',
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
                    'event',
                    models.CharField(
                        choices=[
                            ('edited', 'Редактирано'),
                            ('deleted', 'Изтрито'),
                        ],
                        max_length=12,
                        verbose_name='Действие',
                    ),
                ),
                (
                    'previous_text',
                    models.TextField(blank=True, verbose_name='Предишен текст'),
                ),
                (
                    'actor_name',
                    models.CharField(
                        max_length=300,
                        verbose_name='Име при извършване',
                    ),
                ),
                (
                    'created_at',
                    models.DateTimeField(
                        auto_now_add=True,
                        verbose_name='Извършено на',
                    ),
                ),
                (
                    'actor',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='team_chat_revision_actions',
                        to=settings.AUTH_USER_MODEL,
                        verbose_name='Извършено от',
                    ),
                ),
                (
                    'message',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='revisions',
                        to='blog.teamchatmessage',
                        verbose_name='Съобщение',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Ревизия на съобщение в екипния чат',
                'verbose_name_plural': 'Ревизии на съобщенията в екипния чат',
                'ordering': ('-created_at', '-pk'),
                'default_permissions': (),
            },
        ),
        migrations.CreateModel(
            name='TeamChatReadState',
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
                    'last_read_message_id',
                    models.PositiveBigIntegerField(
                        default=0,
                        verbose_name='Последно прочетено съобщение',
                    ),
                ),
                (
                    'read_at',
                    models.DateTimeField(
                        auto_now=True,
                        verbose_name='Прочетено на',
                    ),
                ),
                (
                    'user',
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='team_chat_read_state',
                        to=settings.AUTH_USER_MODEL,
                        verbose_name='Потребител',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Състояние на екипния чат',
                'verbose_name_plural': 'Състояния на екипния чат',
                'default_permissions': (),
            },
        ),
        migrations.CreateModel(
            name='TeamChatSharedObject',
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
                    'object_id',
                    models.CharField(max_length=64, verbose_name='ID на записа'),
                ),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                (
                    'content_type',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='+',
                        to='contenttypes.contenttype',
                        verbose_name='Тип на записа',
                    ),
                ),
                (
                    'message',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='shared_objects',
                        to='blog.teamchatmessage',
                        verbose_name='Съобщение',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Споделен запис в екипния чат',
                'verbose_name_plural': 'Споделени записи в екипния чат',
                'default_permissions': (),
                'indexes': [
                    models.Index(
                        fields=['content_type', 'object_id'],
                        name='team_chat_shared_obj_idx',
                    ),
                ],
                'constraints': [
                    models.UniqueConstraint(
                        fields=('message', 'content_type', 'object_id'),
                        name='unique_team_chat_shared_obj',
                    ),
                ],
            },
        ),
    ]
