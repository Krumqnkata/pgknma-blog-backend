import re

from django.db import migrations, models
import django.db.models.deletion
from django.utils import timezone


LEGACY_RULES = (
    ('post-published:', 'posts', 'deactivate'),
    ('event-published:', 'event', 'deactivate'),
    ('event-reminder:', 'event', 'deactivate'),
    ('poll-active:', 'pollquestion', 'deactivate'),
    ('changelog-published:', 'changelog', 'deactivate'),
    ('mission-available:', 'mission', 'deactivate'),
    ('comment-reply:', 'comments', 'detach'),
    ('песен-status:', 'bellsongsuggestion', 'detach'),
    ('меме-status:', 'memeofweek', 'detach'),
)


def _legacy_reference(notification):
    key = notification.deduplication_key or ''
    for prefix, model_name, policy in LEGACY_RULES:
        if key.startswith(prefix):
            object_id = key[len(prefix):].split(':', 1)[0]
            if object_id.isdigit():
                return model_name, object_id, policy
    if notification.category == 'gamification_points':
        match = re.fullmatch(r'/post/(\d+)/?', notification.action_url or '')
        if match:
            return 'posts', match.group(1), 'detach'
    return None


def _source_available(model_name, source, now):
    if source is None:
        return False
    if model_name == 'posts':
        return bool(
            source.published
            and source.allowed
            and (source.published_at is None or source.published_at <= now)
        )
    if model_name == 'event':
        return bool(source.published)
    if model_name == 'pollquestion':
        return bool(source.start_date <= now < source.end_date)
    if model_name == 'changelog':
        return bool(source.is_active)
    if model_name == 'mission':
        return bool(source.active and source.start_date <= now < source.end_date)
    return True


def backfill_notification_sources(apps, schema_editor):
    Notification = apps.get_model('blog', 'Notification')
    PushDelivery = apps.get_model('blog', 'PushDelivery')
    ContentType = apps.get_model('contenttypes', 'ContentType')
    database = schema_editor.connection.alias
    now = timezone.now()
    cancel_ids = []

    for notification in Notification.objects.using(database).filter(
        source_content_type__isnull=True,
    ).iterator(chunk_size=500):
        reference = _legacy_reference(notification)
        if reference is None:
            continue
        model_name, object_id, policy = reference
        model = apps.get_model('blog', model_name)
        content_type, _ = ContentType.objects.using(database).get_or_create(
            app_label='blog',
            model=model._meta.model_name,
        )
        source = model.objects.using(database).filter(pk=object_id).first()
        values = {
            'source_content_type_id': content_type.pk,
            'source_object_id': str(object_id),
            'source_policy': policy,
        }

        available = _source_available(model_name, source, now)
        if policy == 'deactivate' and not available:
            values.update(enabled=False, source_unavailable_at=now)
            cancel_ids.append(notification.pk)
        elif policy == 'detach' and source is None:
            values.update(action_url='', source_unavailable_at=now)
            cancel_ids.append(notification.pk)
        elif model_name == 'comments' and source is not None:
            values['action_url'] = f'/post/{source.post_id}#comment-{source.pk}'

        Notification.objects.using(database).filter(pk=notification.pk).update(**values)

    if cancel_ids:
        PushDelivery.objects.using(database).filter(
            notification_id__in=cancel_ids,
            status__in=('pending', 'failed'),
        ).update(status='cancelled', cancelled_at=now)


class Migration(migrations.Migration):

    dependencies = [
        ('contenttypes', '0002_remove_content_type_name'),
        ('blog', '0067_changelog_notifications'),
    ]

    operations = [
        migrations.AddField(
            model_name='notification',
            name='source_content_type',
            field=models.ForeignKey(
                blank=True,
                editable=False,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='+',
                to='contenttypes.contenttype',
                verbose_name='Тип на източника',
            ),
        ),
        migrations.AddField(
            model_name='notification',
            name='source_object_id',
            field=models.CharField(
                blank=True,
                editable=False,
                max_length=64,
                null=True,
                verbose_name='ID на източника',
            ),
        ),
        migrations.AddField(
            model_name='notification',
            name='source_policy',
            field=models.CharField(
                blank=True,
                choices=[
                    ('deactivate', 'Скрий при недостъпен източник'),
                    ('detach', 'Запази историята без връзка'),
                ],
                default='',
                editable=False,
                max_length=12,
                verbose_name='Поведение при недостъпен източник',
            ),
        ),
        migrations.AddField(
            model_name='notification',
            name='source_unavailable_at',
            field=models.DateTimeField(
                blank=True,
                editable=False,
                null=True,
                verbose_name='Източникът е недостъпен от',
            ),
        ),
        migrations.AddIndex(
            model_name='notification',
            index=models.Index(
                fields=['source_content_type', 'source_object_id'],
                name='notification_source_idx',
            ),
        ),
        migrations.RunPython(
            backfill_notification_sources,
            migrations.RunPython.noop,
        ),
    ]

