from django.contrib import admin
from django.contrib.auth.models import Group, User
from django.test import RequestFactory, TestCase
from django.urls import reverse

from .admin_roles import sync_admin_roles
from .ai.polls import next_week_start, week_bounds, week_start
from .models import Event, PollDraft, PollQuestion, Posts


class AttentionDashboardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        sync_admin_roles(reset=True)

    def setUp(self):
        self.admin = User.objects.create_superuser('ux-admin', 'ux@example.com', 'pass')

    def test_superuser_sees_pending_counts_and_poll_status(self):
        PollDraft.objects.create(
            week_start=next_week_start(),
            title='Чернова',
            option_a='1', option_b='2', option_c='3', option_d='4',
            correct_key='a',
        )
        self.client.force_login(self.admin)

        response = self.client.get(reverse('admin:index'))

        self.assertContains(response, 'Какво чака теб')
        self.assertContains(response, 'Няма активна анкета.')
        self.assertContains(response, '1 ИИ чернови чакат избор')
        # Existing start page parts stay.
        self.assertContains(response, 'Бързи действия')
        self.assertContains(response, 'Преди да направите промяна')
        self.assertContains(response, 'id="quote-text"')

    def test_shows_current_poll_title(self):
        start, end = week_bounds(week_start())
        PollQuestion.objects.create(title='Активна викторина', start_date=start, end_date=end)
        self.client.force_login(self.admin)

        response = self.client.get(reverse('admin:index'))

        self.assertContains(response, 'Активна викторина')

    def test_empty_queue_message(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('admin:index'))
        self.assertContains(response, 'Всичко е прегледано')

    def test_pending_post_is_counted(self):
        Posts.objects.create(title='Чака', hook='h', content='c', published=True, allowed=False)
        self.client.force_login(self.admin)
        response = self.client.get(reverse('admin:index'))
        self.assertContains(response, 'Публикации за одобрение')

    def test_author_does_not_see_moderation_queue_or_polls(self):
        user = User.objects.create_user('ux-author', password='pass', is_staff=True)
        user.groups.add(Group.objects.get(name='Автори'))
        self.client.force_login(user)

        response = self.client.get(reverse('admin:index'))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Песни за звънеца')
        self.assertNotContains(response, 'Седмична анкета</h3>')


class GroupedFormTests(TestCase):
    """Every field of the simplified forms is in a named section (no „Други“)."""

    def setUp(self):
        self.request = RequestFactory().get('/admin/')
        self.request.user = User.objects.create_superuser('ux-root', 'root@example.com', 'pass')

    def _section_titles(self, model, obj=None):
        model_admin = admin.site._registry[model]
        return [title for title, _ in model_admin.get_fieldsets(self.request, obj)]

    def test_post_form_sections(self):
        titles = self._section_titles(Posts)
        self.assertEqual(titles[0], 'Основно')
        self.assertNotIn('Други', titles)

    def test_event_form_sections(self):
        self.assertNotIn('Други', self._section_titles(Event))

    def test_poll_form_sections(self):
        self.assertNotIn('Други', self._section_titles(PollQuestion))

    def test_add_pages_render(self):
        self.client.force_login(self.request.user)
        for name in ('admin:blog_posts_add', 'admin:blog_event_add', 'admin:blog_pollquestion_add'):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)
