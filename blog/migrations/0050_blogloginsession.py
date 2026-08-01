import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('blog', '0049_memeofweek_status'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='BlogLoginSession',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('auth_method', models.CharField(choices=[('password', 'Парола'), ('google', 'Google')], max_length=16, verbose_name='Метод за вход')),
                ('ip_address', models.GenericIPAddressField(blank=True, null=True, verbose_name='IP адрес')),
                ('network_operator', models.CharField(blank=True, default='', max_length=255, verbose_name='Мрежов оператор')),
                ('network_asn', models.BigIntegerField(blank=True, null=True, verbose_name='ASN')),
                ('device_label', models.CharField(blank=True, default='', max_length=120, verbose_name='Устройство')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Вход на')),
                ('last_seen_at', models.DateTimeField(verbose_name='Последна активност')),
                ('expires_at', models.DateTimeField(verbose_name='Изтича на')),
                ('revoked_at', models.DateTimeField(blank=True, null=True, verbose_name='Прекратена на')),
                ('revoked_reason', models.CharField(blank=True, choices=[('logout', 'Изход'), ('user', 'Прекратена от потребителя'), ('password_change', 'Смяна на парола'), ('account_deactivated', 'Деактивиран профил'), ('admin', 'Прекратена от администратор')], default='', max_length=32, verbose_name='Причина за прекратяване')),
                ('refresh_jti', models.CharField(blank=True, editable=False, max_length=255, null=True, unique=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='blog_login_sessions', to=settings.AUTH_USER_MODEL, verbose_name='Потребител')),
            ],
            options={
                'verbose_name': 'Сесия за вход в блога',
                'verbose_name_plural': 'Сесии за вход в блога',
                'ordering': ('-created_at',),
                'indexes': [models.Index(fields=['user', 'revoked_at', 'expires_at'], name='blog_session_active_idx'), models.Index(fields=['user', 'created_at'], name='blog_session_history_idx')],
            },
        ),
    ]
