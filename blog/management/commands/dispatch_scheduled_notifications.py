from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from blog.automatic_notifications import (
    notify_event_if_published,
    notify_event_reminder_if_due,
    notify_mission_if_available,
    notify_poll_if_active,
    notify_post_if_due,
)
from blog.meme_weeks import finalize_meme_weeks
from blog.models import Event, Mission, PollQuestion, Posts
from blog.notification_sources import synchronize_all_notification_sources
from blog.push import (
    cancel_expired_notification_deliveries,
    retry_pending_push_notifications,
)


class Command(BaseCommand):
    help = 'Изпраща известия за планирано съдържание, напомняния и чакащи push доставки.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--reminder-hours',
            type=int,
            default=int(getattr(settings, 'WEB_PUSH_EVENT_REMINDER_HOURS', 24)),
            help='Колко часа преди събитие да се изпрати напомняне.',
        )

    def handle(self, *args, **options):
        now = timezone.now()
        reminder_hours = max(1, options['reminder_hours'])
        counts = {'posts': 0, 'events': 0, 'reminders': 0, 'polls': 0, 'missions': 0, 'meme_weeks': 0}

        post_ids = Posts.objects.filter(
            published=True,
            allowed=True,
            publication_notified_at__isnull=True,
        ).filter(
            Q(published_at__isnull=True) | Q(published_at__lte=now)
        ).values_list('pk', flat=True)
        for post_id in post_ids.iterator():
            counts['posts'] += bool(notify_post_if_due(post_id, now=now))

        event_ids = Event.objects.filter(
            published=True,
            publication_notified_at__isnull=True,
        ).values_list('pk', flat=True)
        for event_id in event_ids.iterator():
            counts['events'] += bool(notify_event_if_published(event_id, now=now))

        reminder_ids = Event.objects.filter(
            published=True,
            reminder_notified_at__isnull=True,
            start_datetime__gt=now,
            start_datetime__lte=now + timedelta(hours=reminder_hours),
        ).values_list('pk', flat=True)
        for event_id in reminder_ids.iterator():
            counts['reminders'] += bool(notify_event_reminder_if_due(
                event_id,
                now=now,
                reminder_hours=reminder_hours,
            ))

        poll_ids = PollQuestion.objects.filter(
            activation_notified_at__isnull=True,
            start_date__lte=now,
            end_date__gt=now,
        ).values_list('pk', flat=True)
        for poll_id in poll_ids.iterator():
            counts['polls'] += bool(notify_poll_if_active(poll_id, now=now))

        mission_ids = Mission.objects.filter(
            active=True,
            availability_notified_at__isnull=True,
            start_date__lte=now,
            end_date__gt=now,
        ).values_list('pk', flat=True)
        for mission_id in mission_ids.iterator():
            counts['missions'] += bool(notify_mission_if_available(mission_id, now=now))

        # Closes finished meme weeks: winner, bonus points and announcement.
        counts['meme_weeks'] = len(finalize_meme_weeks(now=now))

        source_sync = synchronize_all_notification_sources(now=now)
        expired_pushes = cancel_expired_notification_deliveries(now=now)
        retry = retry_pending_push_notifications()
        self.stdout.write(self.style.SUCCESS(
            'Notifications: '
            + ', '.join(f'{key}={value}' for key, value in counts.items())
            + f"; push_sent={retry['sent']}, push_failed={retry['failed']}, "
              f"push_throttled={retry['throttled']}, "
              f"push_expired={expired_pushes}; "
              + ', '.join(
                  f'sources_{key}={value}' for key, value in source_sync.items()
              )
        ))
