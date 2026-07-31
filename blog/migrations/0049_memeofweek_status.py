from django.db import migrations, models


def migrate_approved_memes(apps, schema_editor):
    MemeOfWeek = apps.get_model('blog', 'MemeOfWeek')
    MemeOfWeek.objects.filter(is_approved=True).update(status='approved')


class Migration(migrations.Migration):

    dependencies = [
        ('blog', '0048_scheduleimport'),
    ]

    operations = [
        migrations.AddField(
            model_name='memeofweek',
            name='status',
            field=models.CharField(
                choices=[
                    ('pending', 'Чакащо одобрение'),
                    ('approved', 'Одобрено'),
                    ('rejected', 'Отхвърлено'),
                ],
                default='pending',
                help_text='Статус на мемето: изчакващо, одобрено или отхвърлено.',
                max_length=10,
                verbose_name='Статус',
            ),
        ),
        migrations.RunPython(migrate_approved_memes, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='memeofweek',
            name='is_approved',
        ),
    ]
