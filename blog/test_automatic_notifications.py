from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import RequestFactory
from django.utils import timezone
from rest_framework.test import APITestCase

from .models import (
    BellSongSuggestion,
    Category,
    Changelog,
    Comments,
    Event,
    MemeOfWeek,
    Mission,
    Notification,
    PollQuestion,
    Posts,
)
from .notification_categories import (
    COMMENT_REPLY,
    CONTENT_CHANGELOG,
    CONTENT_EVENT,
    CONTENT_POLL,
    CONTENT_POST,
    EVENT_REMINDER,
    MISSION_AVAILABLE,
    SECURITY_ACCOUNT,
    SECURITY_LOGIN,
    SUBMISSION_STATUS,
)
from .session_auth import issue_session_tokens


class AutomaticContentNotificationTests(APITestCase):
    def setUp(self):
        self.now = timezone.now()
        self.category = Category.objects.create(
            full_name='Новини',
            short_name='automatic-notification-news',
        )

    def test_scheduled_post_notifies_once_when_it_becomes_public(self):
        post = Posts.objects.create(
            title='Планирана публикация',
            category=self.category,
            hook='Кратко описание',
            content='Съдържание',
            published=True,
            allowed=True,
            published_at=self.now + timedelta(hours=1),
        )
        self.assertFalse(Notification.objects.filter(category=CONTENT_POST).exists())

        Posts.objects.filter(pk=post.pk).update(published_at=self.now - timedelta(minutes=1))
        call_command('dispatch_scheduled_notifications')

        self.assertEqual(Notification.objects.filter(category=CONTENT_POST).count(), 1)
        post.refresh_from_db()
        self.assertIsNotNone(post.publication_notified_at)
        post.title = 'Редактирано заглавие'
        post.save()
        self.assertEqual(Notification.objects.filter(category=CONTENT_POST).count(), 1)

    def test_event_publication_and_reminder_are_separate_and_once_only(self):
        event = Event.objects.create(
            title='Училищно събитие',
            start_datetime=self.now + timedelta(hours=3),
            location='Актова зала',
            category='Училищно',
            description='Описание',
            attendees_text='Всички',
            published=True,
        )
        self.assertEqual(Notification.objects.filter(category=CONTENT_EVENT).count(), 1)

        Event.objects.filter(pk=event.pk).update(
            publication_notified_at=self.now - timedelta(hours=2),
        )
        call_command('dispatch_scheduled_notifications', reminder_hours=24)
        self.assertEqual(Notification.objects.filter(category=EVENT_REMINDER).count(), 1)
        call_command('dispatch_scheduled_notifications', reminder_hours=24)
        self.assertEqual(Notification.objects.filter(category=EVENT_REMINDER).count(), 1)

    def test_future_poll_and_mission_notify_when_their_start_time_arrives(self):
        poll = PollQuestion.objects.create(
            title='Бъдеща анкета',
            start_date=self.now + timedelta(hours=1),
            end_date=self.now + timedelta(days=2),
        )
        mission = Mission.objects.create(
            slug='future-notification-mission',
            title='Бъдеща мисия',
            description='Описание',
            start_date=self.now + timedelta(hours=1),
            end_date=self.now + timedelta(days=2),
            reward_points=10,
            requirements=[],
        )
        self.assertFalse(Notification.objects.filter(category=CONTENT_POLL).exists())
        self.assertFalse(Notification.objects.filter(category=MISSION_AVAILABLE).exists())

        PollQuestion.objects.filter(pk=poll.pk).update(start_date=self.now - timedelta(minutes=1))
        Mission.objects.filter(pk=mission.pk).update(start_date=self.now - timedelta(minutes=1))
        call_command('dispatch_scheduled_notifications')

        self.assertEqual(Notification.objects.filter(category=CONTENT_POLL).count(), 1)
        mission_notification = Notification.objects.get(category=MISSION_AVAILABLE)
        self.assertEqual(mission_notification.audience, Notification.Audience.AUTHENTICATED)

    def test_active_changelog_entry_notifies_once_and_links_to_changelog(self):
        entry = Changelog.objects.create(
            content='1. Добавени са нови функции.',
            is_active=True,
        )

        notification = Notification.objects.get(category=CONTENT_CHANGELOG)
        self.assertEqual(notification.action_url, '/changelog')
        self.assertEqual(notification.audience, Notification.Audience.PUBLIC)
        entry.refresh_from_db()
        self.assertIsNotNone(entry.notification_notified_at)

        entry.content = '1. Редактиран текст на същия запис.'
        entry.save()
        self.assertEqual(Notification.objects.filter(category=CONTENT_CHANGELOG).count(), 1)

    def test_inactive_changelog_notifies_only_after_first_activation(self):
        entry = Changelog.objects.create(content='Скрити промени', is_active=False)
        self.assertFalse(Notification.objects.filter(category=CONTENT_CHANGELOG).exists())

        entry.is_active = True
        entry.save()
        self.assertEqual(Notification.objects.filter(category=CONTENT_CHANGELOG).count(), 1)

        entry.is_active = False
        entry.save()
        entry.is_active = True
        entry.save()
        self.assertEqual(Notification.objects.filter(category=CONTENT_CHANGELOG).count(), 1)


class PersonalAutomaticNotificationTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='owner', password='Original-pass-123!')
        self.other = User.objects.create_user(username='reply-author')
        self.category = Category.objects.create(
            full_name='Лични известия',
            short_name='personal-notifications',
        )
        self.post = Posts.objects.create(
            title='Публикация с коментари',
            category=self.category,
            hook='Кратко описание',
            content='Съдържание',
            published=True,
            allowed=True,
        )

    def test_reply_notifies_only_parent_comment_author(self):
        parent = Comments.objects.create(content='Начален коментар', user=self.user, post=self.post)
        reply = Comments.objects.create(
            content='Отговор',
            user=self.other,
            post=self.post,
            parent=parent,
        )

        notification = Notification.objects.get(category=COMMENT_REPLY)
        self.assertEqual(notification.user, self.user)
        self.assertIn(str(reply.post_id), notification.action_url)

    def test_song_and_meme_status_changes_notify_without_duplicate_edits(self):
        song = BellSongSuggestion.objects.create(
            user=self.user,
            title='Предложена песен',
            link='https://example.com/song',
        )
        meme = MemeOfWeek.objects.create(user=self.user, title='Предложено меме')

        song.status = 'approved'
        song.save()
        meme.status = 'rejected'
        meme.save()
        self.assertEqual(Notification.objects.filter(category=SUBMISSION_STATUS).count(), 2)

        song.note = 'Само редакция'
        song.save()
        self.assertEqual(Notification.objects.filter(category=SUBMISSION_STATUS).count(), 2)

        song.status = 'rejected'
        song.save()
        self.assertEqual(Notification.objects.filter(category=SUBMISSION_STATUS).count(), 3)

    @patch('blog.session_auth.lookup_network_operator', return_value=('', None))
    def test_only_a_new_device_after_the_first_login_creates_security_alert(self, _lookup):
        factory = RequestFactory()
        first = factory.post(
            '/',
            HTTP_USER_AGENT='Mozilla/5.0 Windows Chrome/120.0',
            HTTP_X_BLOG_DEVICE_ID='11111111-1111-4111-8111-111111111111',
        )
        first.META['REMOTE_ADDR'] = '127.0.0.1'
        issue_session_tokens(self.user, first, 'password')
        self.assertFalse(Notification.objects.filter(category=SECURITY_LOGIN).exists())

        same = factory.post(
            '/',
            HTTP_USER_AGENT='Mozilla/5.0 Windows Chrome/120.0',
            HTTP_X_BLOG_DEVICE_ID='11111111-1111-4111-8111-111111111111',
        )
        same.META['REMOTE_ADDR'] = '127.0.0.1'
        issue_session_tokens(self.user, same, 'password')
        self.assertFalse(Notification.objects.filter(category=SECURITY_LOGIN).exists())

        different = factory.post(
            '/',
            HTTP_USER_AGENT='Mozilla/5.0 Windows Chrome/120.0',
            HTTP_X_BLOG_DEVICE_ID='22222222-2222-4222-8222-222222222222',
        )
        different.META['REMOTE_ADDR'] = '127.0.0.1'
        issue_session_tokens(self.user, different, 'password')
        self.assertEqual(Notification.objects.filter(category=SECURITY_LOGIN).count(), 1)

    def test_password_change_creates_important_profile_notification(self):
        self.client.force_authenticate(self.user)
        response = self.client.put('/api/auth/profile/change-password/', {
            'current_password': 'Original-pass-123!',
            'new_password': 'Much-Newer-pass-938!',
            'new_password_confirm': 'Much-Newer-pass-938!',
        }, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(Notification.objects.filter(
            user=self.user,
            category=SECURITY_ACCOUNT,
        ).exists())
