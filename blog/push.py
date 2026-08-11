import json
import logging

from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from django.utils.html import strip_tags
from django.utils.text import Truncator
from pywebpush import WebPushException, webpush

from .models import Notification, NotificationPreference, PushSubscription


logger = logging.getLogger(__name__)

PREFERENCE_FIELD_BY_CATEGORY = {
    'gamification_points': 'points_enabled',
    'gamification_level': 'levels_enabled',
    'gamification_badge': 'badges_enabled',
    'gamification_mission': 'missions_enabled',
    'gamification_reward': 'rewards_enabled',
}


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

    preference_field = PREFERENCE_FIELD_BY_CATEGORY.get(notification.category)
    if preference_field:
        queryset = queryset.exclude(
            user__notification_preferences__isnull=False,
            **{f'user__notification_preferences__{preference_field}': False},
        )
    else:
        queryset = queryset.exclude(
            user__notification_preferences__isnull=False,
            user__notification_preferences__general_enabled=False,
        )
    return queryset


def _response_status(error):
    response = getattr(error, 'response', None)
    return getattr(response, 'status_code', None)


def send_notification_push(notification_id):
    """Send one in-site notification to each eligible browser subscription."""

    if not web_push_is_configured():
        return {'sent': 0, 'failed': 0, 'removed': 0}

    notification = Notification.objects.filter(pk=notification_id, enabled=True).first()
    if notification is None:
        return {'sent': 0, 'failed': 0, 'removed': 0}

    body = Truncator(strip_tags(notification.text)).chars(180)
    payload = json.dumps({
        'title': 'ПГКНМА Блог',
        'body': body,
        'url': notification.action_url or '/',
        'icon': '/icon-192.png',
        'badge': '/icon-192.png',
        'tag': f'notification-{notification.pk}',
    }, ensure_ascii=False)
    result = {'sent': 0, 'failed': 0, 'removed': 0}

    for subscription in _eligible_subscriptions(notification).iterator():
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
            result['failed'] += 1
            logger.exception('Unexpected Web Push error for subscription %s.', subscription.pk)
        else:
            subscription.failure_count = 0
            subscription.last_success_at = timezone.now()
            subscription.save(update_fields=('failure_count', 'last_success_at', 'updated_at'))
            result['sent'] += 1

    return result
