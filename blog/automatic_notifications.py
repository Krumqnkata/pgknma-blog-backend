from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import Event, Mission, Notification, PollQuestion, Posts
from .notification_categories import (
    CONTENT_EVENT,
    CONTENT_POLL,
    CONTENT_POST,
    EVENT_REMINDER,
    MISSION_AVAILABLE,
)


def create_notification_once(*, key, text, category, action_url, user=None, audience=None):
    defaults = {
        'text': text,
        'category': category,
        'action_url': action_url,
        'user': user,
        'audience': audience or Notification.Audience.PUBLIC,
        'enabled': True,
    }
    notification, created = Notification.objects.get_or_create(
        deduplication_key=key,
        defaults=defaults,
    )
    return notification, created


def notify_post_if_due(post_id, *, now=None):
    now = now or timezone.now()
    with transaction.atomic():
        post = Posts.objects.select_for_update().filter(pk=post_id).first()
        if (
            post is None
            or post.publication_notified_at
            or not post.published
            or not post.allowed
            or (post.published_at and post.published_at > now)
        ):
            return None
        notification, _ = create_notification_once(
            key=f'post-published:{post.pk}',
            text=f'Нова публикация: {post.title}',
            category=CONTENT_POST,
            action_url=f'/post/{post.pk}',
        )
        Posts.objects.filter(pk=post.pk).update(publication_notified_at=now)
        return notification


def notify_event_if_published(event_id, *, now=None):
    now = now or timezone.now()
    with transaction.atomic():
        event = Event.objects.select_for_update().filter(pk=event_id).first()
        if event is None or event.publication_notified_at or not event.published:
            return None
        notification, _ = create_notification_once(
            key=f'event-published:{event.pk}',
            text=f'Ново събитие: {event.title}',
            category=CONTENT_EVENT,
            action_url='/events',
        )
        Event.objects.filter(pk=event.pk).update(publication_notified_at=now)
        return notification


def notify_event_reminder_if_due(event_id, *, now=None, reminder_hours=24):
    now = now or timezone.now()
    reminder_until = now + timedelta(hours=reminder_hours)
    with transaction.atomic():
        event = Event.objects.select_for_update().filter(pk=event_id).first()
        if (
            event is None
            or event.reminder_notified_at
            or not event.published
            or event.start_datetime <= now
            or event.start_datetime > reminder_until
            or (
                event.publication_notified_at
                and event.publication_notified_at > now - timedelta(hours=1)
            )
        ):
            return None
        notification, _ = create_notification_once(
            key=f'event-reminder:{event.pk}',
            text=f'Напомняне: „{event.title}“ започва скоро.',
            category=EVENT_REMINDER,
            action_url='/events',
        )
        Event.objects.filter(pk=event.pk).update(reminder_notified_at=now)
        return notification


def notify_poll_if_active(poll_id, *, now=None):
    now = now or timezone.now()
    with transaction.atomic():
        poll = PollQuestion.objects.select_for_update().filter(pk=poll_id).first()
        if (
            poll is None
            or poll.activation_notified_at
            or poll.start_date > now
            or poll.end_date <= now
        ):
            return None
        notification, _ = create_notification_once(
            key=f'poll-active:{poll.pk}',
            text=f'Нова активна анкета: {poll.title}',
            category=CONTENT_POLL,
            action_url='/weekly-poll',
        )
        PollQuestion.objects.filter(pk=poll.pk).update(activation_notified_at=now)
        return notification


def notify_mission_if_available(mission_id, *, now=None):
    now = now or timezone.now()
    with transaction.atomic():
        mission = Mission.objects.select_for_update().filter(pk=mission_id).first()
        if (
            mission is None
            or mission.availability_notified_at
            or not mission.active
            or mission.start_date > now
            or mission.end_date <= now
        ):
            return None
        notification, _ = create_notification_once(
            key=f'mission-available:{mission.pk}',
            text=f'Нова мисия с ограничен срок: {mission.title}',
            category=MISSION_AVAILABLE,
            action_url='/profile?tab=progress',
            audience=Notification.Audience.AUTHENTICATED,
        )
        Mission.objects.filter(pk=mission.pk).update(availability_notified_at=now)
        return notification
