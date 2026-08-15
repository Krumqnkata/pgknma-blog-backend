from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('auth', '0012_alter_user_first_name_max_length'),
        ('blog', '0073_team_chat_discussions_and_attachments'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='teamchatdiscussion',
            name='visibility',
            field=models.CharField(
                choices=[
                    ('team', 'Целият екип'),
                    ('groups', 'Избрани роли'),
                    ('private', 'Конкретни хора'),
                ],
                db_index=True,
                default='team',
                max_length=12,
                verbose_name='Достъп',
            ),
        ),
        migrations.AddField(
            model_name='teamchatdiscussion',
            name='allowed_groups',
            field=models.ManyToManyField(
                blank=True,
                related_name='allowed_team_chat_discussions',
                to='auth.group',
                verbose_name='Роли с достъп',
            ),
        ),
        migrations.AddField(
            model_name='teamchatdiscussion',
            name='allowed_users',
            field=models.ManyToManyField(
                blank=True,
                related_name='allowed_team_chat_discussions',
                to=settings.AUTH_USER_MODEL,
                verbose_name='Потребители с достъп',
            ),
        ),
        migrations.AlterModelOptions(
            name='teamchatdiscussion',
            options={
                'default_permissions': (),
                'ordering': ('-updated_at', '-pk'),
                'permissions': (
                    (
                        'manage_team_chat_discussion_access',
                        'Може да управлява достъпа до екипни дискусии',
                    ),
                ),
                'verbose_name': 'Дискусия в екипния чат',
                'verbose_name_plural': 'Дискусии в екипния чат',
            },
        ),
    ]
