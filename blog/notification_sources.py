"""Lifecycle tracking for notifications that point at database content."""

from collections import defaultdict
import re

from django.contrib.contenttypes.models import ContentType
from django.db.models import Q
from django.utils import timezone

from .models import (
    BellSongSuggestion,
    Changelog,
    Comments,
    Event,
    MemeOfWeek,
    Mission,
    Notification,
    PollQuestion,
    Posts,
)
from .notification_categories import CONTENT_EVENT, EVENT_REMINDER
from .notification_retention import (
    default_notification_expiry,
    notification_is_expired,
)
from .push import cancel_notification_deliveries


def notification_source_defaults(source, policy):
    """Return fields suitable for creating a structured source relation."""

    if source is None or source.pk is None:
        return {}
    return {
        'source_content_type': ContentType.objects.get_for_model(
            source,
            for_concrete_model=False,
        ),
        'source_object_id': str(source.pk),
        'source_policy': policy,
        'source_unavailable_at': None,
    }


def source_is_available(source, *, now=None):
    """Match the public availability rules used by the frontend APIs."""

    now = now or timezone.now()
    if isinstance(source, Posts):
        return bool(
            source.published
            and source.allowed
            and (source.published_at is None or source.published_at <= now)
        )
    if isinstance(source, Event):
        return bool(source.published)
    if isinstance(source, PollQuestion):
        return bool(source.start_date <= now < source.end_date)
    if isinstance(source, Changelog):
        return bool(source.is_active)
    if isinstance(source, Mission):
        return bool(source.active and source.start_date <= now < source.end_date)
    # Comments and user submissions do not have a separate visibility flag.
    # Their historical notifications are detached only when the row is gone.
    return True


def _sync_reference(content_type, object_id, *, source=None, deleted=False, now=None):
    now = now or timezone.now()
    queryset = Notification.objects.filter(
        source_content_type=content_type,
        source_object_id=str(object_id),
    )
    stats = {'deactivated': 0, 'restored': 0, 'detached': 0, 'cancelled': 0}
    if not queryset.exists():
        return stats

    if isinstance(source, Event) and not deleted:
        queryset.filter(
            important=False,
            category__in=(CONTENT_EVENT, EVENT_REMINDER),
        ).update(
            expires_at=default_notification_expiry(
                CONTENT_EVENT,
                source=source,
                now=now,
            )
        )

    is_available = not deleted and source is not None and source_is_available(source, now=now)
    deactivate = queryset.filter(
        source_policy=Notification.SourcePolicy.DEACTIVATE,
    )

    if is_available:
        restore_ids = list(
            deactivate.filter(source_unavailable_at__isnull=False).values_list('pk', flat=True)
        )
        if restore_ids:
            stats['restored'] = Notification.objects.filter(pk__in=restore_ids).update(
                enabled=True,
                source_unavailable_at=None,
            )
    else:
        deactivate_ids = list(deactivate.values_list('pk', flat=True))
        if deactivate_ids:
            stats['deactivated'] = Notification.objects.filter(
                pk__in=deactivate_ids,
            ).filter(
                Q(enabled=True) | Q(source_unavailable_at__isnull=True),
            ).update(
                enabled=False,
                source_unavailable_at=now,
            )
            stats['cancelled'] += cancel_notification_deliveries(deactivate_ids)

    if deleted:
        detach_ids = list(queryset.filter(
            source_policy=Notification.SourcePolicy.DETACH,
        ).values_list('pk', flat=True))
        if detach_ids:
            stats['detached'] = Notification.objects.filter(
                pk__in=detach_ids,
            ).filter(
                Q(source_unavailable_at__isnull=True) | ~Q(action_url=''),
            ).update(
                action_url='',
                source_unavailable_at=now,
            )
            stats['cancelled'] += cancel_notification_deliveries(detach_ids)
    return stats


def sync_notifications_for_source(source, *, deleted=False, now=None):
    """Apply the configured policy to every notification for one source."""

    if source is None or source.pk is None:
        return {'deactivated': 0, 'restored': 0, 'detached': 0, 'cancelled': 0}
    content_type = ContentType.objects.get_for_model(
        source,
        for_concrete_model=False,
    )
    return _sync_reference(
        content_type,
        source.pk,
        source=source,
        deleted=deleted,
        now=now,
    )


def synchronize_all_notification_sources(*, now=None):
    """Repair missed bulk updates and time-based availability transitions."""

    now = now or timezone.now()
    notifications = Notification.objects.exclude(
        source_content_type__isnull=True,
    ).exclude(
        source_object_id__isnull=True,
    ).select_related('source_content_type')
    grouped = defaultdict(list)
    for notification in notifications.iterator():
        grouped[notification.source_content_type_id].append(notification)

    totals = {'deactivated': 0, 'restored': 0, 'detached': 0, 'cancelled': 0}
    deactivate_ids = []
    restore_ids = []
    detach_ids = []
    cancel_ids = set()
    for rows in grouped.values():
        content_type = rows[0].source_content_type
        model = content_type.model_class() if content_type else None
        objects = {}
        if model is not None:
            prepared_ids = []
            for notification in rows:
                try:
                    prepared_ids.append(
                        model._meta.pk.to_python(notification.source_object_id)
                    )
                except (TypeError, ValueError):
                    continue
            objects = model._base_manager.in_bulk(prepared_ids)

        for notification in rows:
            source = None
            if model is not None:
                try:
                    source = objects.get(
                        model._meta.pk.to_python(notification.source_object_id)
                    )
                except (TypeError, ValueError):
                    source = None
            if notification.source_policy == Notification.SourcePolicy.DEACTIVATE:
                if source is None or not source_is_available(source, now=now):
                    cancel_ids.add(notification.pk)
                    if notification.enabled or notification.source_unavailable_at is None:
                        deactivate_ids.append(notification.pk)
                elif notification.source_unavailable_at is not None:
                    restore_ids.append(notification.pk)
            elif (
                notification.source_policy == Notification.SourcePolicy.DETACH
                and source is None
            ):
                cancel_ids.add(notification.pk)
                if notification.action_url or notification.source_unavailable_at is None:
                    detach_ids.append(notification.pk)

    if deactivate_ids:
        totals['deactivated'] = Notification.objects.filter(
            pk__in=deactivate_ids,
        ).update(enabled=False, source_unavailable_at=now)
    if restore_ids:
        totals['restored'] = Notification.objects.filter(pk__in=restore_ids).update(
            enabled=True,
            source_unavailable_at=None,
        )
    if detach_ids:
        totals['detached'] = Notification.objects.filter(pk__in=detach_ids).update(
            action_url='',
            source_unavailable_at=now,
        )
    totals['cancelled'] = cancel_notification_deliveries(cancel_ids)
    return totals


def notification_target_is_available(notification, *, now=None):
    """Return whether a notification action is safe to open right now."""

    now = now or timezone.now()
    if (
        not notification.enabled
        or not notification.action_url
        or notification_is_expired(notification, now=now)
    ):
        return False
    if not notification.source_content_type_id or not notification.source_object_id:
        return True
    if notification.source_unavailable_at is not None:
        return False
    source = notification.source_object
    if source is None:
        return False
    if notification.source_policy == Notification.SourcePolicy.DEACTIVATE:
        return source_is_available(source, now=now)
    return True


LEGACY_KEY_RULES = (
    ('post-published:', Posts, Notification.SourcePolicy.DEACTIVATE),
    ('event-published:', Event, Notification.SourcePolicy.DEACTIVATE),
    ('event-reminder:', Event, Notification.SourcePolicy.DEACTIVATE),
    ('poll-active:', PollQuestion, Notification.SourcePolicy.DEACTIVATE),
    ('changelog-published:', Changelog, Notification.SourcePolicy.DEACTIVATE),
    ('mission-available:', Mission, Notification.SourcePolicy.DEACTIVATE),
    ('comment-reply:', Comments, Notification.SourcePolicy.DETACH),
    ('песен-status:', BellSongSuggestion, Notification.SourcePolicy.DETACH),
    ('меме-status:', MemeOfWeek, Notification.SourcePolicy.DETACH),
)


def infer_legacy_notification_source(notification):
    """Infer only old rows; live notification handling never parses URLs."""

    key = notification.deduplication_key or ''
    for prefix, model, policy in LEGACY_KEY_RULES:
        if not key.startswith(prefix):
            continue
        object_id = key[len(prefix):].split(':', 1)[0]
        if object_id.isdigit():
            return model, object_id, policy

    if notification.category == 'gamification_points':
        match = re.fullmatch(r'/post/(\d+)/?', notification.action_url or '')
        if match:
            return Posts, match.group(1), Notification.SourcePolicy.DETACH
    return None


def backfill_legacy_notification_sources():
    """Link and clean notification rows created before source tracking."""

    stats = {'linked': 0, 'missing': 0, 'deactivated': 0, 'detached': 0, 'cancelled': 0}
    missing_ids = []
    candidates = Notification.objects.filter(source_content_type__isnull=True)
    for notification in candidates.iterator():
        inferred = infer_legacy_notification_source(notification)
        if inferred is None:
            continue
        model, object_id, policy = inferred
        content_type = ContentType.objects.get_for_model(model, for_concrete_model=False)
        source = model._base_manager.filter(pk=object_id).first()
        notification.source_content_type = content_type
        notification.source_object_id = str(object_id)
        notification.source_policy = policy
        update_fields = ['source_content_type', 'source_object_id', 'source_policy']
        if source is None:
            notification.source_unavailable_at = timezone.now()
            update_fields.append('source_unavailable_at')
            if policy == Notification.SourcePolicy.DEACTIVATE:
                notification.enabled = False
                update_fields.append('enabled')
                stats['deactivated'] += 1
            else:
                notification.action_url = ''
                update_fields.append('action_url')
                stats['detached'] += 1
            missing_ids.append(notification.pk)
            stats['missing'] += 1
        notification.save(update_fields=update_fields)
        stats['linked'] += 1
    stats['cancelled'] = cancel_notification_deliveries(missing_ids)
    return stats
