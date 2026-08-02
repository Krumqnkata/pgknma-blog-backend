import blog.avatars
from django.conf import settings
from django.db import migrations, models


def create_missing_profiles(apps, schema_editor):
    User = apps.get_model('auth', 'User')
    UserProfile = apps.get_model('blog', 'UserProfile')
    existing_user_ids = set(
        UserProfile.objects.values_list('user_id', flat=True)
    )
    UserProfile.objects.bulk_create(
        [
            UserProfile(user_id=user_id)
            for user_id in User.objects.values_list('pk', flat=True)
            if user_id not in existing_user_ids
        ],
        batch_size=500,
    )


class Migration(migrations.Migration):

    dependencies = [
        ('blog', '0052_alter_postimage_options_postimage_alt_text_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='userprofile',
            options={
                'verbose_name': 'Профилна снимка',
                'verbose_name_plural': 'Профилни снимки',
            },
        ),
        migrations.AddField(
            model_name='userprofile',
            name='avatar',
            field=models.ImageField(
                blank=True,
                help_text='Локално съхранена и оптимизирана WebP профилна снимка.',
                null=True,
                upload_to=blog.avatars.profile_avatar_upload_path,
                validators=[blog.avatars.validate_avatar_upload_size],
                verbose_name='Профилна снимка',
            ),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='avatar_preset',
            field=models.CharField(
                blank=True,
                choices=[
                    ('code', 'Код'),
                    ('terminal', 'Терминал'),
                    ('robot', 'Робот'),
                    ('calculator', 'Калкулатор'),
                    ('geometry', 'Геометрия'),
                    ('atom', 'Наука'),
                    ('books', 'Книги'),
                    ('pencil', 'Творчество'),
                    ('music', 'Музика'),
                    ('gamepad', 'Игри'),
                    ('rocket', 'Ракета'),
                    ('idea', 'Идея'),
                ],
                default='',
                max_length=24,
                verbose_name='Готов аватар',
            ),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='avatar_source',
            field=models.CharField(
                choices=[
                    ('initials', 'Инициали'),
                    ('upload', 'Качена снимка'),
                    ('preset', 'Готов аватар'),
                    ('google', 'Google снимка'),
                ],
                default='initials',
                max_length=10,
                verbose_name='Източник на аватара',
            ),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='avatar_updated_at',
            field=models.DateTimeField(
                blank=True,
                null=True,
                verbose_name='Аватарът е променен на',
            ),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='avatar_user_selected',
            field=models.BooleanField(
                default=False,
                help_text=(
                    'Предотвратява автоматичен Google импорт след избор от '
                    'потребителя или нулиране от администратор.'
                ),
                verbose_name='Направен изричен избор',
            ),
        ),
        migrations.RunPython(create_missing_profiles, migrations.RunPython.noop),
    ]
