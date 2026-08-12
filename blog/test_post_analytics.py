from datetime import datetime, timedelta, timezone as datetime_timezone

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from .models import Category, PageView, Posts
from .post_analytics import current_site_month_bounds


@override_settings(TIME_ZONE='Europe/Sofia')
class PostMonthlyViewsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.author = User.objects.create_user(
            username='monthly-views-author',
            password='A-strong-test-password-2026',
        )
        self.category = Category.objects.create(
            full_name='Новини',
            short_name='monthly-views-news',
        )
        self.post = Posts.objects.create(
            title='Месечна статистика',
            category=self.category,
            hook='Кратко описание',
            content='Съдържание',
            author=self.author,
            published=True,
            allowed=True,
        )

    def _create_view_at(self, value: datetime) -> None:
        page_view = PageView.objects.create(
            path=f'/post/{self.post.pk}',
            post=self.post,
        )
        PageView.objects.filter(pk=page_view.pk).update(created_at=value)

    def test_list_and_detail_include_only_current_calendar_month_views(self):
        month_start, _ = current_site_month_bounds()
        self._create_view_at(month_start + timedelta(hours=1))
        self._create_view_at(month_start + timedelta(days=2))
        self._create_view_at(month_start - timedelta(seconds=1))

        list_response = self.client.get(reverse('posts-list'))
        detail_response = self.client.get(reverse('posts-detail', args=[self.post.pk]))

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(list_response.json()[0]['views_this_month'], 2)
        self.assertEqual(detail_response.json()['views_this_month'], 2)

    def test_month_boundary_uses_bulgarian_local_time(self):
        instant = datetime(2026, 8, 31, 21, 30, tzinfo=datetime_timezone.utc)

        start, end = current_site_month_bounds(instant)

        self.assertEqual(start.isoformat(), '2026-09-01T00:00:00+03:00')
        self.assertEqual(end.isoformat(), '2026-10-01T00:00:00+03:00')
