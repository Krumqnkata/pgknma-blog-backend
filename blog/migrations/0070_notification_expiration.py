from datetime import timedelta

from django.db import migrations, models
from django.utils import timezone


CATEGORY_RETENTION_DAYS = {
    'general': 30,
    'content_post': 7,
    'content_event': 7,
    'event_reminder': 7,
    'content_poll': 7,
    'content_changelog': 7,
    'comment_reply': 14,
    'submission_status': 14,
    'mission_available': 7,
    'security_login': 14,
    'security_account': 14,
}


def _retention_days(category):
    if (category or '').startswith('gamification_'):
        return 7
    return CATEGORY_RETENTION_DAYS.get(category, 14)


def backfill_notification_expiration(apps, schema_editor):
    Notification = apps.get_model('blog', 'Notification')
    Event = apps.get_model('blog', 'Event')

    event_ids = set()
    event_source_ids = Notification.objects.filter(
        category__in=('content_event', 'event_reminder'),
    ).values_list('source_object_id', flat=True)
    for source_object_id in event_source_ids.iterator(chunk_size=1000):
        try:
            event_ids.add(int(source_object_id))
        except (TypeError, ValueError):
            continue
    events = Event.objects.in_bulk(event_ids)

    now = timezone.now()
    pending_updates = []
    for notification in Notification.objects.all().iterator(chunk_size=1000):
        expires_at = None
        if notification.category in ('content_event', 'event_reminder'):
            try:
                event = events.get(int(notification.source_object_id))
            except (TypeError, ValueError):
                event = None
            if event is not None:
                boundary = event.end_datetime or event.start_datetime
                if boundary is not None:
                    expires_at = boundary + timedelta(days=2)

        if expires_at is None:
            expires_at = (notification.created_at or now) + timedelta(
                days=_retention_days(notification.category),
            )
        notification.expires_at = expires_at
        pending_updates.append(notification)
        if len(pending_updates) >= 1000:
            Notification.objects.bulk_update(
                pending_updates,
                ('expires_at',),
                batch_size=1000,
            )
            pending_updates.clear()

    if pending_updates:
        Notification.objects.bulk_update(
            pending_updates,
            ('expires_at',),
            batch_size=1000,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('blog', '0069_lifetime_profile_activity'),
    ]

    operations = [
        migrations.AddField(
            model_name='notification',
            name='important',
            field=models.BooleanField(
                default=False,
                help_text=(
                    'Важните известия остават активни, докато не бъдат '
                    'изключени или архивирани ръчно.'
                ),
                verbose_name='Важно / без автоматично архивиране',
            ),
        ),
        migrations.AddField(
            model_name='notification',
            name='expires_at',
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                help_text=(
                    'След тази дата известието се скрива от камбанката и се '
                    'премества в архива. Оставете празно за автоматичен срок '
                    'според категорията.'
                ),
                null=True,
                verbose_name='Автоматично архивиране на',
            ),
        ),
        migrations.RunPython(
            backfill_notification_expiration,
            migrations.RunPython.noop,
        ),
    ]
