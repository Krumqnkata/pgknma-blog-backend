"""Fourth round of admin UX: dates, Bulgarian labels, quick moderation, cleanup page, guide."""

from datetime import timedelta

from django.contrib.auth.models import Permission, User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .admin_help import help_url_for_path
from .models import (
    BellSongSuggestion,
    Category,
    ContactSubmission,
    Event,
    MemeOfWeek,
    Notification,
    PollQuestion,
    SiteSettings,
)

SONG_LINK = 'https://www.youtube.com/watch?v=dQw4w9WgXcQ'


class SuperuserTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = User.objects.create_superuser('round3-admin', 'r3@example.com', 'pass')
        self.client.force_login(self.admin)


class DateAndLabelTests(SuperuserTestCase):
    def test_lists_show_day_month_year(self):
        ContactSubmission.objects.create(name='Иван', email='i@example.com', reason='general', message='Здравейте')
        content = self.client.get(reverse('admin:blog_contactsubmission_changelist')).content.decode()
        self.assertIn(timezone.localdate().strftime('%d.%m.%Y'), content)
        self.assertNotIn('преди обяд', content)
        self.assertNotIn('след обяд', content)

    def test_event_form_accepts_bulgarian_date(self):
        response = self.client.post(reverse('admin:blog_event_add'), {
            'title': 'Концерт',
            'category': 'Academic',
            'description': 'Описание',
            'start_datetime_0': '15.11.2030',
            'start_datetime_1': '10:30',
            'end_datetime_0': '',
            'end_datetime_1': '',
            'location': 'Зала',
            'attendees_text': 'Всички',
            'published': 'on',
        })
        self.assertEqual(response.status_code, 302, response.content.decode()[:2000])
        event = Event.objects.get(title='Концерт')
        start = timezone.localtime(event.start_datetime)
        self.assertEqual((start.day, start.month, start.year, start.hour, start.minute), (15, 11, 2030, 10, 30))

    def test_remaining_lists_have_bulgarian_headers(self):
        Category.objects.create(full_name='Новини', short_name='novini')
        content = self.client.get(reverse('admin:blog_category_changelist')).content.decode()
        self.assertIn('Пълно име', content)
        self.assertNotIn('Full name', content)
        content = self.client.get(reverse('admin:blog_missionactivity_changelist')).content.decode()
        self.assertNotIn('Reference id', content)
        self.assertNotIn('Created at', content)

    def test_delete_page_and_settings_title_are_bulgarian(self):
        category = Category.objects.create(full_name='За триене', short_name='za-triene')
        content = self.client.get(reverse('admin:blog_category_delete', args=[category.pk])).content.decode()
        self.assertIn('Сигурни ли сте', content)
        self.assertNotIn('Are you sure', content)

        settings_obj = SiteSettings.objects.create()
        content = self.client.get(reverse('admin:blog_sitesettings_change', args=[settings_obj.pk])).content.decode()
        self.assertNotIn('SiteSettings object', content)

    def test_new_user_page_hint_is_bulgarian(self):
        content = self.client.get(reverse('admin:auth_user_add')).content.decode()
        self.assertNotIn('After you', content)

    def test_post_categories_are_checkboxes(self):
        Category.objects.create(full_name='Спорт', short_name='sport')
        content = self.client.get(reverse('admin:blog_posts_add')).content.decode()
        self.assertIn('name="categories" value=', content)
        self.assertIn('type="checkbox" name="categories"', content)
        self.assertNotIn('selectfilter', content.lower().replace('selectfilterstacked', ''))


class QuickStatusTests(SuperuserTestCase):
    def setUp(self):
        super().setUp()
        self.student = User.objects.create_user('student')
        self.song = BellSongSuggestion.objects.create(user=self.student, title='Песен', link=SONG_LINK)

    def url(self, status, model='bellsongsuggestion', pk=None):
        return reverse(f'admin:blog_{model}_quick_status', args=(pk or self.song.pk, status))

    def test_pending_rows_have_buttons(self):
        content = self.client.get(reverse('admin:blog_bellsongsuggestion_changelist')).content.decode()
        self.assertIn('✓ Одобри', content)
        self.assertIn(self.url('approved'), content)

    def test_approve_changes_status_and_notifies_student(self):
        response = self.client.post(self.url('approved'))
        self.assertEqual(response.status_code, 302)
        self.song.refresh_from_db()
        self.assertEqual(self.song.status, 'approved')
        self.assertTrue(Notification.objects.filter(user=self.student).exists())

    def test_reject_meme(self):
        meme = MemeOfWeek.objects.create(user=self.student, title='Меме')
        self.client.post(self.url('rejected', model='memeofweek', pk=meme.pk))
        meme.refresh_from_db()
        self.assertEqual(meme.status, 'rejected')

    def test_get_and_unknown_status_are_refused(self):
        self.assertEqual(self.client.get(self.url('approved')).status_code, 405)
        self.assertEqual(self.client.post(self.url('deleted')).status_code, 404)

    def test_needs_change_permission(self):
        viewer = User.objects.create_user('viewer', password='pass', is_staff=True)
        viewer.user_permissions.add(Permission.objects.get(codename='view_bellsongsuggestion'))
        self.client.force_login(viewer)
        self.assertEqual(self.client.post(self.url('approved')).status_code, 403)
        self.song.refresh_from_db()
        self.assertEqual(self.song.status, 'pending')


class CleanupPageTests(TestCase):
    def setUp(self):
        cache.clear()
        self.settings_obj = SiteSettings.objects.create()
        self.user = User.objects.create_user('cleaner', password='pass', is_staff=True)
        self.user.user_permissions.add(Permission.objects.get(codename='change_sitesettings'))
        self.client.force_login(self.user)

    def test_needs_a_delete_permission(self):
        self.assertEqual(self.client.get(reverse('admin:blog_sitesettings_cleanup')).status_code, 403)

    def test_page_lists_polls_and_settings_page_only_links(self):
        self.user.user_permissions.add(Permission.objects.get(codename='delete_pollquestion'))
        start = timezone.now()
        PollQuestion.objects.create(title='Стара анкета', start_date=start, end_date=start + timedelta(days=1))

        content = self.client.get(reverse('admin:blog_sitesettings_cleanup')).content.decode()
        self.assertIn('Стара анкета', content)
        self.assertIn(reverse('admin:blog_sitesettings_delete_polls', args=(self.settings_obj.pk,)), content)

        content = self.client.get(
            reverse('admin:blog_sitesettings_change', args=(self.settings_obj.pk,)),
        ).content.decode()
        self.assertNotIn('related-deletion-section', content)
        self.assertIn(reverse('admin:blog_sitesettings_cleanup'), content)

    def test_delete_returns_to_cleanup_page(self):
        self.user.user_permissions.add(Permission.objects.get(codename='delete_pollquestion'))
        start = timezone.now()
        poll = PollQuestion.objects.create(title='Изтрий', start_date=start, end_date=start + timedelta(days=1))
        response = self.client.post(
            reverse('admin:blog_sitesettings_delete_polls', args=(self.settings_obj.pk,)),
            {f'polls-poll_{poll.pk}': 'on'},
        )
        self.assertRedirects(response, reverse('admin:blog_sitesettings_cleanup'), fetch_redirect_response=False)
        self.assertFalse(PollQuestion.objects.filter(pk=poll.pk).exists())


class ListAndUserPageTests(SuperuserTestCase):
    def test_upcoming_events_come_before_past_ones(self):
        now = timezone.now()
        common = {'location': 'Зала', 'description': 'd', 'attendees_text': 'Всички'}
        Event.objects.create(title='Минало събитие', start_datetime=now - timedelta(days=2), **common)
        Event.objects.create(title='Далечно събитие', start_datetime=now + timedelta(days=9), **common)
        Event.objects.create(title='Близко събитие', start_datetime=now + timedelta(days=1), **common)

        content = self.client.get(reverse('admin:blog_event_changelist')).content.decode()

        near = content.index('Близко събитие')
        self.assertLess(near, content.index('Далечно събитие'))
        self.assertLess(content.index('Далечно събитие'), content.index('Минало събитие'))

    def test_superuser_has_full_access_label_and_folded_permissions(self):
        content = self.client.get(reverse('admin:auth_user_changelist')).content.decode()
        self.assertIn('Пълен достъп', content)

        content = self.client.get(reverse('admin:auth_user_change', args=[self.admin.pk])).content.decode()
        self.assertIn('Отделни права (рядко нужни)', content)
        self.assertIn('Роля и достъп', content)


class GuideTests(SuperuserTestCase):
    def test_help_button_points_to_the_screen_section(self):
        help_url = reverse('admin-help')
        self.assertEqual(help_url_for_path(reverse('admin:blog_posts_changelist')), f'{help_url}#guide-posts')
        self.assertEqual(help_url_for_path(reverse('admin:valentine_profile_changelist')), f'{help_url}#guide-valentine-profiles')
        self.assertEqual(help_url_for_path(reverse('admin:index')), f'{help_url}#guide-start')
        self.assertIsNone(help_url_for_path(help_url))

        content = self.client.get(reverse('admin:blog_posts_changelist')).content.decode()
        self.assertIn(f'{help_url}#guide-posts', content)
        self.assertIn('Помощ за този екран', content)

    def test_help_page_has_search(self):
        content = self.client.get(reverse('admin-help')).content.decode()
        self.assertIn('id="ux-help-search-input"', content)
        self.assertIn('admin_help.js', content)
