import json
import logging
from datetime import timedelta

from django.conf import settings
from django.db.models import Min
from django.utils import timezone
from django.utils.html import strip_tags
from django.utils.text import Truncator
from pywebpush import WebPushException, webpush

from .models import Notification, NotificationReadState, PushDelivery, PushSubscription
from .notification_categories import (
    DEVICE_PREFERENCE_FIELD_BY_CATEGORY,
    PUBLIC_PUSH_CATEGORIES,
    user_preference_field,
)


logger = logging.getLogger(__name__)


def web_push_is_configured():
    return bool(
        settings.WEB_PUSH_ENABLED
        and settings.VAPID_PUBLIC_KEY
        and settings.VAPID_PRIVATE_KEY
        and settings.VAPID_SUBJECT
    )


def _eligible_subscriptions(notification):
    queryset = PushSubscription.objects.filter(active=True).select_related('user')
    if notification.user_id:
        queryset = queryset.filter(user_id=notification.user_id)
    elif notification.audience == Notification.Audience.AUTHENTICATED:
        queryset = queryset.filter(user__isnull=False)
    elif notification.category not in PUBLIC_PUSH_CATEGORIES:
        # Anonymous devices receive only the explicitly supported public topics.
        queryset = queryset.filter(user__isnull=False)

    preference_field = user_preference_field(notification.category)
    queryset = queryset.exclude(
        user__isnull=False,
        user__notification_preferences__isnull=False,
        **{f'user__notification_preferences__{preference_field}': False},
    )
    device_preference = DEVICE_PREFERENCE_FIELD_BY_CATEGORY.get(notification.category)
    if device_preference:
        queryset = queryset.filter(**{device_preference: True})
    return queryset


def _public_rate_limit_reached(subscription, notification, now):
    if notification.category not in PUBLIC_PUSH_CATEGORIES:
        return False
    maximum = max(1, int(getattr(settings, 'WEB_PUSH_MAX_PUBLIC_PER_HOUR', 10)))
    return PushDelivery.objects.filter(
        subscription=subscription,
        status=PushDelivery.Status.SENT,
        sent_at__gte=now - timedelta(hours=1),
        notification__category__in=PUBLIC_PUSH_CATEGORIES,
    ).count() >= maximum


def _response_status(error):
    response = getattr(error, 'response', None)
    return getattr(response, 'status_code', None)


def snapshot_notification_deliveries(notification_id):
    """Freeze the notification recipients at the moment it is created."""

    notification = Notification.objects.filter(pk=notification_id, enabled=True).first()
    if notification is None:
        return 0

    subscription_ids = _eligible_subscriptions(notification).values_list('pk', flat=True)
    deliveries = [
        PushDelivery(subscription_id=subscription_id, notification=notification)
        for subscription_id in subscription_ids
    ]
    return len(PushDelivery.objects.bulk_create(deliveries, ignore_conflicts=True))


def cancel_notification_deliveries_for_user(user_id, notification_ids):
    """Stop unsent deliveries after the user reads or archives notifications."""

    notification_ids = list(notification_ids)
    if not user_id or not notification_ids:
        return 0
    return PushDelivery.objects.filter(
        subscription__user_id=user_id,
        notification_id__in=notification_ids,
        status__in=(PushDelivery.Status.PENDING, PushDelivery.Status.FAILED),
    ).update(
        status=PushDelivery.Status.CANCELLED,
        cancelled_at=timezone.now(),
    )


def cancel_read_deliveries_for_subscription(subscription):
    """Cancel old queued pushes when an anonymous device is linked to an account."""

    if not subscription.user_id:
        return 0
    read_notification_ids = NotificationReadState.objects.filter(
        user_id=subscription.user_id,
    ).values_list('notification_id', flat=True)
    return PushDelivery.objects.filter(
        subscription=subscription,
        notification_id__in=read_notification_ids,
        status__in=(PushDelivery.Status.PENDING, PushDelivery.Status.FAILED),
    ).update(
        status=PushDelivery.Status.CANCELLED,
        cancelled_at=timezone.now(),
    )


def send_notification_push(notification_id):
    """Send only deliveries snapshotted when the notification was created."""

    if not web_push_is_configured():
        return {'sent': 0, 'failed': 0, 'removed': 0, 'throttled': 0, 'skipped': 0}

    notification = Notification.objects.filter(pk=notification_id, enabled=True).first()
    if notification is None:
        return {'sent': 0, 'failed': 0, 'removed': 0, 'throttled': 0, 'skipped': 0}

    body = Truncator(strip_tags(notification.text)).chars(180)
    payload = json.dumps({
        'title': 'ПГКНМА Блог',
        'body': body,
        'url': notification.action_url or '/',
        'icon': '/icon-192.png',
        'badge': '/icon-192.png',
        'tag': f'notification-{notification.pk}',
    }, ensure_ascii=False)
    result = {'sent': 0, 'failed': 0, 'removed': 0, 'throttled': 0, 'skipped': 0}
    now = timezone.now()

    eligible_ids = set(_eligible_subscriptions(notification).values_list('pk', flat=True))
    deliveries = PushDelivery.objects.filter(
        notification=notification,
        status__in=(PushDelivery.Status.PENDING, PushDelivery.Status.FAILED),
        attempts__lt=3,
    ).select_related('subscription')

    for delivery in deliveries.iterator():
        subscription = delivery.subscription
        if subscription.pk not in eligible_ids:
            delivery.status = PushDelivery.Status.CANCELLED
            delivery.cancelled_at = now
            delivery.save(update_fields=('status', 'cancelled_at'))
            result['skipped'] += 1
            continue
        if (
            delivery.last_attempt_at
            and delivery.last_attempt_at > now - timedelta(minutes=5)
        ):
            result['skipped'] += 1
            continue
        if _public_rate_limit_reached(subscription, notification, now):
            result['throttled'] += 1
            continue

        delivery.attempts += 1
        delivery.last_attempt_at = now
        try:
            webpush(
                subscription_info={
                    'endpoint': subscription.endpoint,
                    'keys': {'p256dh': subscription.p256dh, 'auth': subscription.auth},
                },
                data=payload,
                vapid_private_key=settings.VAPID_PRIVATE_KEY,
                vapid_claims={'sub': settings.VAPID_SUBJECT},
                ttl=86400,
            )
        except WebPushException as exc:
            status_code = _response_status(exc)
            if status_code in (404, 410):
                subscription.delete()
                result['removed'] += 1
            else:
                delivery.status = PushDelivery.Status.FAILED
                delivery.save(update_fields=('status', 'attempts', 'last_attempt_at'))
                subscription.failure_count += 1
                subscription.active = subscription.failure_count < 3
                subscription.save(update_fields=('failure_count', 'active', 'updated_at'))
                result['failed'] += 1
                logger.warning(
                    'Web Push failed for subscription %s (HTTP %s).',
                    subscription.pk,
                    status_code,
                )
        except Exception:
            delivery.status = PushDelivery.Status.FAILED
            delivery.save(update_fields=('status', 'attempts', 'last_attempt_at'))
            result['failed'] += 1
            logger.exception('Unexpected Web Push error for subscription %s.', subscription.pk)
        else:
            delivery.status = PushDelivery.Status.SENT
            delivery.sent_at = now
            delivery.save(update_fields=('status', 'attempts', 'last_attempt_at', 'sent_at'))
            subscription.failure_count = 0
            subscription.last_success_at = now
            subscription.save(update_fields=('failure_count', 'last_success_at', 'updated_at'))
            result['sent'] += 1

    return result


def retry_pending_push_notifications(*, max_age_hours=24, limit=100):
    """Retry only previously snapshotted, unsent deliveries."""

    cutoff = timezone.now() - timedelta(hours=max_age_hours)
    queued_notifications = PushDelivery.objects.filter(
        status__in=(PushDelivery.Status.PENDING, PushDelivery.Status.FAILED),
        attempts__lt=3,
        created_at__gte=cutoff,
        notification__enabled=True,
        subscription__active=True,
    ).values('notification_id').annotate(
        first_queued_at=Min('created_at'),
    ).order_by('first_queued_at')[:limit]
    notification_ids = [row['notification_id'] for row in queued_notifications]
    summary = {'notifications': 0, 'sent': 0, 'failed': 0, 'removed': 0, 'throttled': 0}
    for notification_id in notification_ids:
        result = send_notification_push(notification_id)
        summary['notifications'] += 1
        for key in ('sent', 'failed', 'removed', 'throttled'):
            summary[key] += result.get(key, 0)
    return summary
