from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import RequestFactory
from django.urls import reverse
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
    PushDelivery,
    PushSubscription,
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
from .gamification.services import change_points


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
        post_notification = Notification.objects.get(category=CONTENT_POST)
        self.assertAlmostEqual(
            (post_notification.expires_at - timezone.now()).total_seconds(),
            timedelta(days=7).total_seconds(),
            delta=5,
        )
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
        event_notification = Notification.objects.get(category=CONTENT_EVENT)
        self.assertEqual(
            event_notification.expires_at,
            event.start_datetime + timedelta(days=2),
        )

        Event.objects.filter(pk=event.pk).update(
            publication_notified_at=self.now - timedelta(hours=2),
        )
        call_command('dispatch_scheduled_notifications', reminder_hours=24)
        reminder_notification = Notification.objects.get(category=EVENT_REMINDER)
        self.assertEqual(
            reminder_notification.expires_at,
            event.start_datetime + timedelta(days=2),
        )
        call_command('dispatch_scheduled_notifications', reminder_hours=24)
        self.assertEqual(Notification.objects.filter(category=EVENT_REMINDER).count(), 1)

        event.end_datetime = self.now + timedelta(hours=5)
        event.save()
        event_notification.refresh_from_db()
        reminder_notification.refresh_from_db()
        expected_expiry = event.end_datetime + timedelta(days=2)
        self.assertEqual(event_notification.expires_at, expected_expiry)
        self.assertEqual(reminder_notification.expires_at, expected_expiry)

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


class NotificationSourceLifecycleTests(APITestCase):
    def setUp(self):
        self.now = timezone.now()
        self.user = User.objects.create_user(username='source-owner')
        self.other = User.objects.create_user(username='source-replier')
        self.category = Category.objects.create(
            full_name='Източници на известия',
            short_name='notification-sources',
        )

    def create_public_post(self):
        return Posts.objects.create(
            title='Публикация с известие',
            category=self.category,
            hook='Кратко описание',
            content='Съдържание',
            published=True,
            allowed=True,
        )

    def test_deleting_public_content_hides_notice_and_cancels_pending_push(self):
        post = self.create_public_post()
        notification = Notification.objects.get(category=CONTENT_POST)
        self.assertEqual(notification.source_object, post)
        self.assertEqual(
            notification.source_policy,
            Notification.SourcePolicy.DEACTIVATE,
        )
        subscription = PushSubscription.objects.create(
            endpoint='https://push.example.test/deleted-source',
            p256dh='public-key',
            auth='auth-key',
        )
        delivery = PushDelivery.objects.create(
            subscription=subscription,
            notification=notification,
        )

        post.delete()

        notification.refresh_from_db()
        delivery.refresh_from_db()
        self.assertFalse(notification.enabled)
        self.assertIsNotNone(notification.source_unavailable_at)
        self.assertEqual(delivery.status, PushDelivery.Status.CANCELLED)
        self.assertEqual(
            self.client.get(
                reverse('notification-target', args=(notification.pk,)),
            ).status_code,
            410,
        )
        listed_ids = {
            row['id'] for row in self.client.get(reverse('notification-list')).data
        }
        self.assertNotIn(notification.pk, listed_ids)

    def test_hiding_and_republishing_content_updates_existing_notice(self):
        post = self.create_public_post()
        notification = Notification.objects.get(category=CONTENT_POST)

        post.published = False
        post.save(update_fields=('published',))
        notification.refresh_from_db()
        self.assertFalse(notification.enabled)
        self.assertIsNotNone(notification.source_unavailable_at)

        post.published = True
        post.save(update_fields=('published',))
        notification.refresh_from_db()
        self.assertTrue(notification.enabled)
        self.assertIsNone(notification.source_unavailable_at)
        self.assertEqual(Notification.objects.filter(category=CONTENT_POST).count(), 1)
        response = self.client.get(
            reverse('notification-target', args=(notification.pk,)),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['action_url'], f'/post/{post.pk}')

    def test_deleted_reply_keeps_history_but_removes_invalid_action(self):
        post = self.create_public_post()
        parent = Comments.objects.create(
            content='Начален коментар',
            user=self.user,
            post=post,
        )
        reply = Comments.objects.create(
            content='Отговор',
            user=self.other,
            post=post,
            parent=parent,
        )
        notification = Notification.objects.get(category=COMMENT_REPLY)
        self.assertEqual(notification.source_object, reply)
        self.assertIn(f'#comment-{reply.pk}', notification.action_url)

        reply.delete()

        notification.refresh_from_db()
        self.assertTrue(notification.enabled)
        self.assertEqual(notification.action_url, '')
        self.assertIsNotNone(notification.source_unavailable_at)
        self.client.force_authenticate(self.user)
        listed_ids = {
            row['id'] for row in self.client.get(reverse('notification-list')).data
        }
        self.assertIn(notification.pk, listed_ids)
        self.assertEqual(
            self.client.get(
                reverse('notification-target', args=(notification.pk,)),
            ).status_code,
            410,
        )

    def test_periodic_command_hides_expired_poll_notification(self):
        poll = PollQuestion.objects.create(
            title='Кратка анкета',
            start_date=self.now - timedelta(hours=1),
            end_date=self.now + timedelta(hours=1),
        )
        notification = Notification.objects.get(category=CONTENT_POLL)
        PollQuestion.objects.filter(pk=poll.pk).update(
            end_date=self.now - timedelta(minutes=1),
        )

        call_command('dispatch_scheduled_notifications')

        notification.refresh_from_db()
        self.assertFalse(notification.enabled)
        self.assertIsNotNone(notification.source_unavailable_at)

    def test_post_points_notice_keeps_history_without_deleted_post_link(self):
        post = self.create_public_post()
        change_points(
            user=self.user,
            points=3,
            action='post_read',
            description='Прочетена публикация',
            reference_type=Posts._meta.label_lower,
            reference_id=post.pk,
            evaluate_badges=False,
        )
        notification = Notification.objects.get(category='gamification_points')
        self.assertEqual(notification.source_object, post)

        post.delete()

        notification.refresh_from_db()
        self.assertTrue(notification.enabled)
        self.assertEqual(notification.action_url, '')
        self.assertIsNotNone(notification.source_unavailable_at)

    def test_repair_command_links_and_cleans_legacy_notice(self):
        draft = Posts.objects.create(
            title='Скрита стара публикация',
            category=self.category,
            hook='Кратко описание',
            content='Съдържание',
            published=False,
            allowed=False,
        )
        legacy = Notification.objects.create(
            text='Старо автоматично известие',
            category=CONTENT_POST,
            action_url=f'/post/{draft.pk}',
            deduplication_key=f'post-published:{draft.pk}',
        )

        call_command('repair_notification_sources')

        legacy.refresh_from_db()
        self.assertEqual(legacy.source_object, draft)
        self.assertFalse(legacy.enabled)
        self.assertIsNotNone(legacy.source_unavailable_at)
