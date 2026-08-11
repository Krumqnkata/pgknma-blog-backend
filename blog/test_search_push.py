from unittest.mock import patch
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from .models import (
    BellSongSuggestion,
    Category,
    Event,
    MemeOfWeek,
    Notification,
    NotificationPreference,
    Posts,
    PushSubscription,
)
from .push import send_notification_push


class GlobalSearchApiTests(APITestCase):
    def setUp(self):
        self.category = Category.objects.create(full_name='Училищни новини')
        self.public_post = Posts.objects.create(
            title='Олимпиада по математика',
            category=self.category,
            hook='Успех за училищния отбор',
            content='Публично съдържание',
            published=True,
            allowed=True,
        )
        Posts.objects.create(
            title='Олимпиада скрита чернова',
            category=self.category,
            hook='Не трябва да се вижда',
            content='Чернова',
            published=False,
            allowed=True,
        )
        Posts.objects.create(
            title='Олимпиада с бъдеща дата',
            category=self.category,
            hook='Все още скрита',
            content='Насрочена',
            published=True,
            allowed=True,
            published_at=timezone.now() + timedelta(days=1),
        )
        Event.objects.create(
            title='Олимпиада по математика',
            start_datetime=timezone.now(),
            location='Актова зала',
            category='Състезание',
            description='Училищно събитие',
            attendees_text='Всички ученици',
            published=True,
        )
        BellSongSuggestion.objects.create(
            title='Олимпиада песен',
            link='https://example.com/song',
            status='pending',
        )
        MemeOfWeek.objects.create(title='Олимпиада меме', status='pending')

    def test_search_returns_only_public_approved_content(self):
        response = self.client.get('/api/search/?q=Олимпиада')

        self.assertEqual(response.status_code, 200)
        results = response.data['results']
        self.assertEqual(
            {(result['kind'], result['id']) for result in results},
            {('post', self.public_post.pk), ('event', Event.objects.get().pk)},
        )

    def test_short_query_returns_empty_result(self):
        response = self.client.get('/api/search/?q=о')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['results'], [])


class PushSubscriptionApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='push-user', password='secret')
        self.client.force_authenticate(self.user)
        self.payload = {
            'endpoint': 'https://push.example.test/subscription/one',
            'p256dh': 'public-browser-key',
            'auth': 'browser-auth-key',
        }

    @override_settings(WEB_PUSH_ENABLED=True, VAPID_PUBLIC_KEY='public-key')
    def test_public_config_exposes_only_public_vapid_key(self):
        self.client.force_authenticate(None)
        response = self.client.get('/api/push/config/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {'enabled': True, 'public_key': 'public-key'})

    def test_authenticated_user_can_subscribe_and_unsubscribe(self):
        response = self.client.post('/api/push/subscription/', self.payload, format='json')
        self.assertEqual(response.status_code, 201)
        subscription = PushSubscription.objects.get()
        self.assertEqual(subscription.user, self.user)

        response = self.client.get(
            '/api/push/subscription/',
            {'endpoint': self.payload['endpoint']},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['subscribed'])

        response = self.client.delete(
            '/api/push/subscription/',
            {'endpoint': self.payload['endpoint']},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PushSubscription.objects.exists())

    def test_anonymous_user_cannot_register_subscription(self):
        self.client.force_authenticate(None)
        response = self.client.post('/api/push/subscription/', self.payload, format='json')
        self.assertEqual(response.status_code, 401)


@override_settings(
    WEB_PUSH_ENABLED=True,
    VAPID_PUBLIC_KEY='public-key',
    VAPID_PRIVATE_KEY='private-key',
    VAPID_SUBJECT='mailto:test@example.com',
)
class PushDeliveryTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='recipient')
        PushSubscription.objects.create(
            user=self.user,
            endpoint='https://push.example.test/subscription/two',
            p256dh='public-browser-key',
            auth='browser-auth-key',
        )

    @patch('blog.push.webpush')
    def test_new_enabled_notification_is_sent_after_commit(self, webpush_mock):
        with self.captureOnCommitCallbacks(execute=True):
            Notification.objects.create(
                user=self.user,
                text='Автоматично Push известие',
                enabled=True,
            )

        webpush_mock.assert_called_once()

    @patch('blog.push.webpush')
    def test_delivery_uses_eligible_subscription(self, webpush_mock):
        notification = Notification.objects.create(
            user=self.user,
            text='Ново важно известие',
            enabled=False,
        )
        Notification.objects.filter(pk=notification.pk).update(enabled=True)

        result = send_notification_push(notification.pk)

        self.assertEqual(result['sent'], 1)
        webpush_mock.assert_called_once()

    @patch('blog.push.webpush')
    def test_disabled_category_preference_prevents_delivery(self, webpush_mock):
        NotificationPreference.objects.create(user=self.user, general_enabled=False)
        notification = Notification.objects.create(
            user=self.user,
            text='Официално известие',
            enabled=False,
        )
        Notification.objects.filter(pk=notification.pk).update(enabled=True)

        result = send_notification_push(notification.pk)

        self.assertEqual(result['sent'], 0)
        webpush_mock.assert_not_called()
