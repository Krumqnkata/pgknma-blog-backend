from django.db import migrations, models


def mark_existing_changelog_entries_as_notified(apps, schema_editor):
    Changelog = apps.get_model('blog', 'Changelog')
    for entry in Changelog.objects.filter(
        is_active=True,
        notification_notified_at__isnull=True,
    ).iterator(chunk_size=500):
        Changelog.objects.filter(pk=entry.pk).update(
            notification_notified_at=entry.updated_at,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('blog', '0066_notification_archiving_and_cancelled_delivery'),
    ]

    operations = [
        migrations.AddField(
            model_name='changelog',
            name='notification_notified_at',
            field=models.DateTimeField(
                blank=True,
                editable=False,
                help_text='Кога е изпратено автоматичното известие за този запис.',
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='notificationpreference',
            name='changelog_enabled',
            field=models.BooleanField(default=True, verbose_name='Промени по сайта'),
        ),
        migrations.AddField(
            model_name='pushsubscription',
            name='changelog_enabled',
            field=models.BooleanField(default=True, verbose_name='Промени по сайта'),
        ),
        migrations.RunPython(
            mark_existing_changelog_entries_as_notified,
            migrations.RunPython.noop,
        ),
    ]
