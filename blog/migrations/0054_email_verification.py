from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('blog', '0053_userprofile_avatars'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='sitesettings',
            name='auto_verify_on_email_failure',
            field=models.BooleanField(
                default=False,
                help_text=(
                    'Аварийна настройка: ако Resend не успее да изпрати '
                    'писмото, новият акаунт се потвърждава автоматично. '
                    'Оставете изключено при нормална работа.'
                ),
                verbose_name=(
                    'Автоматично потвърждение при проблем с имейл услугата'
                ),
            ),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='email_verified',
            field=models.BooleanField(
                default=True,
                help_text=(
                    'Само публичните регистрации с имейл и парола започват '
                    'като непотвърдени. Google и администраторските акаунти '
                    'са потвърдени.'
                ),
                verbose_name='Потвърден имейл',
            ),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='email_verified_at',
            field=models.DateTimeField(
                blank=True,
                null=True,
                verbose_name='Имейлът е потвърден на',
            ),
        ),
        migrations.CreateModel(
            name='EmailVerificationToken',
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
                    'token_hash',
                    models.CharField(
                        editable=False,
                        max_length=64,
                        unique=True,
                        verbose_name='Хеш на токена',
                    ),
                ),
                (
                    'created_at',
                    models.DateTimeField(
                        auto_now_add=True,
                        verbose_name='Създаден на',
                    ),
                ),
                (
                    'expires_at',
                    models.DateTimeField(verbose_name='Изтича на'),
                ),
                (
                    'used_at',
                    models.DateTimeField(
                        blank=True,
                        null=True,
                        verbose_name='Използван на',
                    ),
                ),
                (
                    'user',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='email_verification_tokens',
                        to=settings.AUTH_USER_MODEL,
                        verbose_name='Потребител',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Токен за потвърждение на имейл',
                'verbose_name_plural': 'Токени за потвърждение на имейли',
                'ordering': ('-created_at',),
                'indexes': [
                    models.Index(
                        fields=['user', 'used_at', 'created_at'],
                        name='blog_email_verify_user_idx',
                    ),
                    models.Index(
                        fields=['expires_at'],
                        name='blog_email_verify_exp_idx',
                    ),
                ],
            },
        ),
    ]
