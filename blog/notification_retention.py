"""Expiry rules shared by the notification API, push delivery and cleanup."""

from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from .notification_categories import (
    COMMENT_REPLY,
    CONTENT_CHANGELOG,
    CONTENT_EVENT,
    CONTENT_MEME,
    CONTENT_POLL,
    CONTENT_POST,
    EVENT_REMINDER,
    GENERAL,
    MISSION_AVAILABLE,
    SECURITY_ACCOUNT,
    SECURITY_LOGIN,
    SUBMISSION_STATUS,
    TEAM_CHAT_INVITE,
)


DEFAULT_RETENTION_DAYS = 14
EVENT_ARCHIVE_GRACE_DAYS = 2

CATEGORY_RETENTION_DAYS = {
    GENERAL: 30,
    CONTENT_POST: 7,
    CONTENT_EVENT: 7,
    EVENT_REMINDER: 7,
    CONTENT_POLL: 7,
    CONTENT_MEME: 7,
    CONTENT_CHANGELOG: 7,
    COMMENT_REPLY: 14,
    SUBMISSION_STATUS: 14,
    MISSION_AVAILABLE: 7,
    SECURITY_LOGIN: 14,
    SECURITY_ACCOUNT: 14,
    TEAM_CHAT_INVITE: 14,
}


def retention_days_for_category(category):
    """Return the active lifetime for a notification category."""

    if (category or '').startswith('gamification_'):
        return 7
    return CATEGORY_RETENTION_DAYS.get(category, DEFAULT_RETENTION_DAYS)


def default_notification_expiry(category, *, source=None, created_at=None, now=None):
    """Calculate the automatic archive time for a newly created notification."""

    now = now or timezone.now()
    if category in (CONTENT_EVENT, EVENT_REMINDER) and source is not None:
        event_boundary = (
            getattr(source, 'end_datetime', None)
            or getattr(source, 'start_datetime', None)
        )
        if event_boundary is not None:
            return event_boundary + timedelta(days=EVENT_ARCHIVE_GRACE_DAYS)

    base = created_at or now
    return base + timedelta(days=retention_days_for_category(category))


def active_notification_q(*, now=None, prefix=''):
    """Build a query that excludes automatically archived notifications."""

    now = now or timezone.now()
    return (
        Q(**{f'{prefix}important': True})
        | Q(**{f'{prefix}expires_at__isnull': True})
        | Q(**{f'{prefix}expires_at__gt': now})
    )


def expired_notification_q(*, now=None, prefix=''):
    """Build a query matching notifications whose active lifetime has ended."""

    now = now or timezone.now()
    return Q(
        **{
            f'{prefix}important': False,
            f'{prefix}expires_at__isnull': False,
            f'{prefix}expires_at__lte': now,
        }
    )


def notification_is_expired(notification, *, now=None):
    """Return whether a single notification is automatically archived."""

    now = now or timezone.now()
    return bool(
        not notification.important
        and notification.expires_at is not None
        and notification.expires_at <= now
    )
