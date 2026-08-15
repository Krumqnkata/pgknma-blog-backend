import blog.models
import blog.team_chat_storage
import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('blog', '0072_team_chat_pins_and_reactions'),
        ('contenttypes', '0002_remove_content_type_name'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='TeamChatDiscussion',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=200, verbose_name='Заглавие')),
                ('created_by_name', models.CharField(max_length=300, verbose_name='Име на създателя')),
                ('source_object_id', models.CharField(blank=True, max_length=64, null=True, verbose_name='ID на административния запис')),
                ('status', models.CharField(choices=[('open', 'Отворена'), ('resolved', 'Приключена')], db_index=True, default='open', max_length=12, verbose_name='Състояние')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Създадена на')),
                ('updated_at', models.DateTimeField(auto_now=True, db_index=True, verbose_name='Обновена на')),
                ('resolved_at', models.DateTimeField(blank=True, null=True, verbose_name='Приключена на')),
                ('resolved_by_name', models.CharField(blank=True, max_length=300, verbose_name='Име при приключване')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_team_chat_discussions', to=settings.AUTH_USER_MODEL, verbose_name='Създадена от')),
                ('origin_message', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='origin_discussion', to='blog.teamchatmessage', verbose_name='Начално съобщение')),
                ('participants', models.ManyToManyField(blank=True, related_name='team_chat_discussions', to=settings.AUTH_USER_MODEL, verbose_name='Участници')),
                ('resolved_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='resolved_team_chat_discussions', to=settings.AUTH_USER_MODEL, verbose_name='Приключена от')),
                ('source_content_type', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='contenttypes.contenttype', verbose_name='Тип на административния запис')),
            ],
            options={
                'verbose_name': 'Дискусия в екипния чат',
                'verbose_name_plural': 'Дискусии в екипния чат',
                'ordering': ('-updated_at', '-pk'),
                'default_permissions': (),
            },
        ),
        migrations.AddField(
            model_name='teamchatmessage',
            name='discussion',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='messages', to='blog.teamchatdiscussion', verbose_name='Дискусия'),
        ),
        migrations.CreateModel(
            name='TeamChatDiscussionReadState',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('last_read_message_id', models.PositiveBigIntegerField(default=0, verbose_name='Последно прочетено съобщение')),
                ('read_at', models.DateTimeField(auto_now=True, verbose_name='Прочетено на')),
                ('discussion', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='read_states', to='blog.teamchatdiscussion', verbose_name='Дискусия')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='team_chat_discussion_read_states', to=settings.AUTH_USER_MODEL, verbose_name='Потребител')),
            ],
            options={
                'verbose_name': 'Прочит на екипна дискусия',
                'verbose_name_plural': 'Прочити на екипни дискусии',
                'default_permissions': (),
            },
        ),
        migrations.CreateModel(
            name='TeamChatAttachment',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('uploaded_by_name', models.CharField(max_length=300, verbose_name='Име на качилия')),
                ('image', models.ImageField(height_field='height', storage=blog.team_chat_storage.TeamChatProtectedStorage(), upload_to=blog.models.team_chat_attachment_upload_path, verbose_name='Изображение', width_field='width')),
                ('thumbnail', models.ImageField(storage=blog.team_chat_storage.TeamChatProtectedStorage(), upload_to=blog.models.team_chat_attachment_thumbnail_upload_path, verbose_name='Миниатюра')),
                ('original_name', models.CharField(blank=True, max_length=255, verbose_name='Оригинално име')),
                ('original_format', models.CharField(max_length=12, verbose_name='Оригинален формат')),
                ('content_type', models.CharField(default='image/webp', max_length=40, verbose_name='MIME тип')),
                ('byte_size', models.PositiveIntegerField(verbose_name='Размер в байтове')),
                ('width', models.PositiveIntegerField(verbose_name='Ширина')),
                ('height', models.PositiveIntegerField(verbose_name='Височина')),
                ('is_animated', models.BooleanField(default=False, verbose_name='Анимирано')),
                ('alt_text', models.CharField(blank=True, max_length=255, verbose_name='Алтернативен текст')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='Качено на')),
                ('message', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='attachments', to='blog.teamchatmessage', verbose_name='Съобщение')),
                ('uploaded_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='team_chat_attachments', to=settings.AUTH_USER_MODEL, verbose_name='Качено от')),
            ],
            options={
                'verbose_name': 'Изображение в екипния чат',
                'verbose_name_plural': 'Изображения в екипния чат',
                'ordering': ('created_at', 'pk'),
                'default_permissions': (),
                'indexes': [models.Index(fields=['uploaded_by', 'created_at'], name='team_attachment_rate_idx')],
            },
        ),
        migrations.AddIndex(
            model_name='teamchatdiscussion',
            index=models.Index(fields=['status', 'updated_at'], name='team_discussion_status_idx'),
        ),
        migrations.AddIndex(
            model_name='teamchatdiscussion',
            index=models.Index(fields=['source_content_type', 'source_object_id'], name='team_discussion_source_idx'),
        ),
        migrations.AddConstraint(
            model_name='teamchatdiscussion',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('source_content_type__isnull', True), ('source_object_id__isnull', True)), models.Q(('source_content_type__isnull', False), ('source_object_id__isnull', False)), _connector='OR'), name='team_chat_discussion_source_pair'),
        ),
        migrations.AddConstraint(
            model_name='teamchatdiscussion',
            constraint=models.CheckConstraint(condition=models.Q(('origin_message__isnull', True), ('source_content_type__isnull', True), _connector='OR'), name='team_discussion_single_origin'),
        ),
        migrations.AddConstraint(
            model_name='teamchatdiscussionreadstate',
            constraint=models.UniqueConstraint(fields=('discussion', 'user'), name='unique_team_discussion_read'),
        ),
    ]
