from datetime import timedelta

from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from blog.models import (
    Badge,
    Category,
    Mission,
    Notification,
    NotificationPreference,
    NotificationReadState,
    PageView,
    PointTransaction,
    Posts,
    PushDelivery,
    PushSubscription,
    UserBadge,
    UserMissionProgress,
    UserProfile,
)
from blog.templatetags.admin_dashboard import admin_statistics_dashboard


class NotificationSyncApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user('reader', password='safe-password')
        self.other_user = User.objects.create_user('other', password='safe-password')
        self.general = Notification.objects.create(
            text='Важно съобщение',
            category='general',
            action_url='/post/42',
        )
        self.badge = Notification.objects.create(
            user=self.user,
            text='Нова значка',
            category='gamification_badge',
            action_url='/profile?tab=progress',
        )
        Notification.objects.create(
            user=self.other_user,
            text='Чуждо известие',
            category='general',
        )
        self.first_device = APIClient()
        self.second_device = APIClient()
        self.first_device.force_authenticate(self.user)
        self.second_device.force_authenticate(self.user)

    def test_read_state_is_shared_between_devices_and_action_is_returned(self):
        response = self.first_device.get(reverse('notification-list'))
        self.assertEqual(response.status_code, 200)
        rows = {row['id']: row for row in response.data}
        self.assertFalse(rows[self.general.pk]['is_read'])
        self.assertEqual(rows[self.general.pk]['action_url'], '/post/42')

        marked = self.first_device.post(
            reverse('notification-read', args=(self.general.pk,)),
        )
        self.assertEqual(marked.status_code, 200)

        second_response = self.second_device.get(reverse('notification-list'))
        second_rows = {row['id']: row for row in second_response.data}
        self.assertTrue(second_rows[self.general.pk]['is_read'])
        self.assertIsNotNone(second_rows[self.general.pk]['read_at'])
        self.assertEqual(
            NotificationReadState.objects.filter(
                user=self.user,
                notification=self.general,
            ).count(),
            1,
        )

    def test_preferences_filter_disabled_categories(self):
        response = self.first_device.patch(
            reverse('notification-preferences'),
            {'badges_enabled': False},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['badges_enabled'])
        self.assertFalse(
            NotificationPreference.objects.get(user=self.user).badges_enabled,
        )

        notification_response = self.second_device.get(reverse('notification-list'))
        ids = {row['id'] for row in notification_response.data}
        self.assertIn(self.general.pk, ids)
        self.assertNotIn(self.badge.pk, ids)

    def test_mark_all_only_marks_the_selected_channel(self):
        response = self.first_device.post(
            reverse('notification-read-all'),
            {'channel': 'gamification'},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['marked_read'], 1)
        self.assertTrue(
            NotificationReadState.objects.filter(
                user=self.user,
                notification=self.badge,
            ).exists(),
        )
        self.assertFalse(
            NotificationReadState.objects.filter(
                user=self.user,
                notification=self.general,
            ).exists(),
        )

    def test_user_cannot_mark_another_users_notification(self):
        private_notification = Notification.objects.get(text='Чуждо известие')
        response = self.first_device.post(
            reverse('notification-read', args=(private_notification.pk,)),
        )
        self.assertEqual(response.status_code, 404)

    def test_archived_notification_moves_out_of_bell_and_can_be_restored(self):
        archived = self.first_device.post(
            reverse('notification-archive', args=(self.general.pk,)),
        )
        self.assertEqual(archived.status_code, 200)
        self.assertTrue(archived.data['is_archived'])

        active_ids = {
            row['id']
            for row in self.second_device.get(reverse('notification-list')).data
        }
        self.assertNotIn(self.general.pk, active_ids)

        archived_response = self.second_device.get(
            reverse('notification-list'),
            {'archived': 'true'},
        )
        archived_rows = {row['id']: row for row in archived_response.data}
        self.assertTrue(archived_rows[self.general.pk]['is_archived'])
        self.assertIsNotNone(archived_rows[self.general.pk]['archived_at'])

        restored = self.second_device.delete(
            reverse('notification-archive', args=(self.general.pk,)),
        )
        self.assertEqual(restored.status_code, 200)
        self.assertFalse(restored.data['is_archived'])
        self.assertIn(
            self.general.pk,
            {row['id'] for row in self.first_device.get(reverse('notification-list')).data},
        )

    def test_archive_read_archives_only_read_notifications_in_channel(self):
        self.first_device.post(reverse('notification-read', args=(self.general.pk,)))

        response = self.first_device.post(
            reverse('notification-archive-read'),
            {'channel': 'general'},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['archived'], 1)
        self.assertIsNotNone(
            NotificationReadState.objects.get(
                user=self.user,
                notification=self.general,
            ).archived_at,
        )
        self.assertFalse(
            NotificationReadState.objects.filter(
                user=self.user,
                notification=self.badge,
                archived_at__isnull=False,
            ).exists(),
        )

    def test_reading_notification_cancels_pending_push_on_other_devices(self):
        first_subscription = PushSubscription.objects.create(
            user=self.user,
            endpoint='https://push.example.test/first',
            p256dh='key-one',
            auth='auth-one',
        )
        second_subscription = PushSubscription.objects.create(
            user=self.user,
            endpoint='https://push.example.test/second',
            p256dh='key-two',
            auth='auth-two',
        )
        PushDelivery.objects.bulk_create([
            PushDelivery(subscription=first_subscription, notification=self.general),
            PushDelivery(subscription=second_subscription, notification=self.general),
        ])

        response = self.first_device.post(
            reverse('notification-read', args=(self.general.pk,)),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            PushDelivery.objects.filter(
                notification=self.general,
                status=PushDelivery.Status.CANCELLED,
            ).count(),
            2,
        )


class PageViewAndAdminDashboardTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            'admin',
            'admin@example.com',
            'safe-password',
        )
        self.category = Category.objects.create(
            full_name='Новини',
            short_name='novini',
        )
        self.post = Posts.objects.create(
            title='Популярна публикация',
            category=self.category,
            hook='Кратко описание',
            content='Съдържание',
            author=self.admin_user,
            published=True,
            allowed=True,
        )

    def test_page_view_endpoint_tracks_post_without_network_metadata(self):
        client = APIClient()
        response = client.post(
            reverse('analytics-page-view'),
            {'path': f'/post/{self.post.pk}'},
            format='json',
        )
        self.assertEqual(response.status_code, 201)
        page_view = PageView.objects.get()
        self.assertEqual(page_view.post, self.post)
        self.assertIsNone(page_view.user)
        self.assertFalse(hasattr(page_view, 'ip_address'))

    def test_dashboard_contains_all_requested_metrics(self):
        now = timezone.now()
        PageView.objects.create(
            path=f'/post/{self.post.pk}',
            post=self.post,
            user=self.admin_user,
        )
        profile, _ = UserProfile.objects.get_or_create(user=self.admin_user)
        profile.gamification_points = 25
        profile.save(update_fields=('gamification_points',))
        PointTransaction.objects.create(
            user=self.admin_user,
            action='admin_test',
            points=25,
            balance_after=25,
            description='Тестови точки',
        )
        badge = Badge.objects.create(
            slug='dashboard-badge',
            title='Dashboard значка',
            description='Тест',
        )
        UserBadge.objects.create(user=self.admin_user, badge=badge)
        mission = Mission.objects.create(
            slug='dashboard-mission',
            title='Dashboard мисия',
            description='Тест',
            start_date=now - timedelta(days=1),
            end_date=now + timedelta(days=1),
            requirements=[],
        )
        UserMissionProgress.objects.create(
            user=self.admin_user,
            mission=mission,
            completed_at=now,
        )

        request = RequestFactory().get('/admin/')
        request.user = self.admin_user
        context = admin_statistics_dashboard({'request': request})

        self.assertEqual(context['new_users'], 1)
        self.assertEqual(context['active_users'], 1)
        self.assertEqual(context['visits_today'], 1)
        self.assertEqual(context['completed_missions'], 1)
        self.assertEqual(context['awarded_badges'], 1)
        self.assertEqual(context['awarded_points'], 25)
        self.assertEqual(context['current_points'], 25)
        self.assertEqual(list(context['top_posts']), [self.post])

        self.client.force_login(self.admin_user)
        dashboard_response = self.client.get(reverse('admin:index'))
        self.assertContains(dashboard_response, 'Статистика на сайта')
        self.assertContains(dashboard_response, 'Популярна публикация')
