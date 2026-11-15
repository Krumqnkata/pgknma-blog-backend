"""Second round of admin UX: lists, team chat messages, start page, translations."""

from datetime import datetime, timedelta
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core.cache import cache
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import admin_badges, team_chat_bot
from .admin_roles import sync_admin_roles
from .ai import polls as ai_polls
from .ai.polls import week_bounds, week_start
from .models import (
    AIContentCheck,
    BellSongSuggestion,
    Comments,
    ContactSubmission,
    Event,
    PollAnswer,
    PollOption,
    PollQuestion,
    Posts,
    SiteSettings,
    TeamChatMessage,
    TeamChatSharedObject,
)

SONG_LINK = 'https://www.youtube.com/watch?v=dQw4w9WgXcQ'


def _bot_messages():
    return TeamChatMessage.objects.filter(author__isnull=True, author_name=team_chat_bot.BOT_NAME)


def _local(*args):
    return timezone.make_aware(datetime(*args))


class ChangeListTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('lists-admin', 'lists@example.com', 'pass')
        self.client.force_login(self.admin)
        cache.clear()

    def test_poll_list_shows_state_answers_and_correct_rate(self):
        start, end = week_bounds(week_start())
        poll = PollQuestion.objects.create(title='Викторина', start_date=start, end_date=end)
        right = PollOption.objects.create(question=poll, key='a', text='Да', is_correct=True)
        wrong = PollOption.objects.create(question=poll, key='b', text='Не')
        for index, option in enumerate((right, right, wrong)):
            user = User.objects.create_user(f'voter{index}')
            PollAnswer.objects.create(user=user, question=poll, selected_option=option)

        response = self.client.get(reverse('admin:blog_pollquestion_changelist'))

        self.assertContains(response, 'ux-badge-ok')
        self.assertContains(response, 'Активна')
        self.assertContains(response, '67%')
        response = self.client.get(reverse('admin:blog_pollquestion_changelist') + '?poll_state=past')
        self.assertNotContains(response, 'Викторина')

    def test_post_list_has_coloured_status_and_views(self):
        Posts.objects.create(title='Чакаща', hook='h', content='c', published=True, allowed=False)
        response = self.client.get(reverse('admin:blog_posts_changelist') + '?post_state=pending')
        self.assertContains(response, 'ux-badge-warn')
        self.assertContains(response, 'Прегледи (30 дни)')

    def test_event_list_says_when(self):
        Event.objects.create(
            title='Утрешно',
            start_datetime=timezone.now() + timedelta(days=1, hours=1),
            location='Зала',
            description='d',
            attendees_text='Всички',
        )
        response = self.client.get(reverse('admin:blog_event_changelist') + '?when=upcoming')
        self.assertContains(response, 'Утрешно')
        self.assertContains(response, 'ux-badge-info')

    def test_flagged_comments_come_first(self):
        post = Posts.objects.create(title='П', hook='h', content='c', published=True, allowed=True)
        Comments.objects.create(content='Обикновен коментар', user=self.admin, post=post)
        flagged = Comments.objects.create(content='Маркиран коментар', user=self.admin, post=post)
        flagged.ai_checks.create(verdict=AIContentCheck.VERDICT_FLAGGED, category='insult')

        content = self.client.get(reverse('admin:blog_comments_changelist')).content.decode()

        self.assertLess(content.index('Маркиран коментар'), content.index('Обикновен коментар'))

    def test_song_status_can_be_changed_from_the_list(self):
        song = BellSongSuggestion.objects.create(user=self.admin, title='Песен', link=SONG_LINK)
        old = BellSongSuggestion.objects.create(user=self.admin, title='Стара', link=SONG_LINK, status='approved')
        url = reverse('admin:blog_bellsongsuggestion_changelist')
        content = self.client.get(url).content.decode()
        self.assertLess(content.index('Песен'), content.index('Стара'))

        response = self.client.post(url, {
            'form-TOTAL_FORMS': '2',
            'form-INITIAL_FORMS': '2',
            'form-0-id': str(song.pk),
            'form-0-status': 'approved',
            'form-1-id': str(old.pk),
            'form-1-status': 'approved',
            '_save': 'Запис',
        })

        self.assertEqual(response.status_code, 302)
        song.refresh_from_db()
        self.assertEqual(song.status, 'approved')

    def test_contact_list_shows_reason_and_preview(self):
        ContactSubmission.objects.create(
            name='Иван', email='i@example.com', reason='bug_report', message='Бутонът не работи на телефон.',
        )
        response = self.client.get(reverse('admin:blog_contactsubmission_changelist'))
        self.assertContains(response, 'Открит бъг')
        self.assertContains(response, 'Бутонът не работи на телефон.')

    def test_meme_list_loads(self):
        self.assertEqual(self.client.get(reverse('admin:blog_memeofweek_changelist')).status_code, 200)


class TeamChatBotTests(TestCase):
    def test_contact_submission_posts_reason_and_attaches_record(self):
        with self.captureOnCommitCallbacks(execute=True):
            submission = ContactSubmission.objects.create(
                name='Мария', email='m@example.com', reason='general', message='Тайно съдържание',
            )

        message = _bot_messages().get()
        self.assertIn('Общо запитване', message.text)
        self.assertNotIn('Мария', message.text)
        self.assertNotIn('Тайно', message.text)
        shared = TeamChatSharedObject.objects.get(message=message)
        self.assertEqual(shared.object_id, str(submission.pk))

    def test_switch_off_stops_messages(self):
        SiteSettings.objects.update_or_create(pk=1, defaults={'team_chat_notify_contact': False})
        with self.captureOnCommitCallbacks(execute=True):
            ContactSubmission.objects.create(name='А', email='a@example.com', reason='general', message='x')
        self.assertFalse(_bot_messages().exists())

    def test_daily_digest_once_and_only_after_eight(self):
        Posts.objects.create(title='Чака', hook='h', content='c', published=True, allowed=False)
        morning = _local(2026, 11, 16, 7, 30)
        later = _local(2026, 11, 16, 9, 30)

        self.assertIsNone(team_chat_bot.daily_pending_digest(now=morning))
        self.assertIsNotNone(team_chat_bot.daily_pending_digest(now=later))
        self.assertIsNone(team_chat_bot.daily_pending_digest(now=later + timedelta(hours=3)))
        self.assertIn('статии за одобрение: 1', _bot_messages().get().text)

    def test_digest_skipped_when_nothing_waits(self):
        self.assertIsNone(team_chat_bot.daily_pending_digest(now=_local(2026, 11, 16, 10, 0)))

    def test_ai_flag_posts_once(self):
        from .ai import moderation

        author = User.objects.create_user('flagged-author')
        post = Posts.objects.create(title='П', hook='h', content='c', published=True, allowed=True)
        comment = Comments.objects.create(content='лошо', user=author, post=post)
        flagged = (AIContentCheck.VERDICT_FLAGGED, 'insult', 'обида')
        with patch.object(moderation, 'check_text', return_value=flagged):
            moderation.check_object(comment, lambda obj: obj.content, 'model')
            moderation.check_object(comment, lambda obj: obj.content, 'model')
        self.assertEqual(_bot_messages().count(), 1)
        self.assertIn('ИИ маркира коментар', _bot_messages().get().text)
        self.assertIn('Обидно', _bot_messages().get().text)

    @override_settings(CF_ACCOUNT_ID='a', CF_API_TOKEN='t')
    def test_poll_drafts_and_auto_publish_are_announced(self):
        from .test_ai_polls import MONDAY, THURSDAY, enable_poll_ai, fake_model

        site_settings = enable_poll_ai()
        with patch('blog.ai.polls.run_chat', side_effect=fake_model()):
            ai_polls.run_weekly_poll_tasks(site_settings, today=THURSDAY)
            ai_polls.run_weekly_poll_tasks(site_settings, today=THURSDAY)
        self.assertEqual(_bot_messages().filter(text__contains='ИИ подготви 3 чернови').count(), 1)

        ai_polls.run_weekly_poll_tasks(site_settings, today=MONDAY)
        self.assertTrue(_bot_messages().filter(text__contains='пусната автоматично').exists())


class StartPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        sync_admin_roles(reset=True)

    def setUp(self):
        cache.clear()
        self.admin = User.objects.create_superuser('start-admin', 'start@example.com', 'pass')

    def test_sidebar_badges_respect_permissions(self):
        Posts.objects.create(title='Чака', hook='h', content='c', published=True, allowed=False)
        request = RequestFactory().get('/admin/')
        request.user = self.admin
        self.assertEqual(admin_badges.posts_pending(request), '1')

        author = User.objects.create_user('badge-author', password='pass', is_staff=True)
        author.groups.add(Group.objects.get(name='Автори'))
        request.user = author
        self.assertIsNone(admin_badges.songs_pending(request))

    def test_my_drafts_and_system_status(self):
        Posts.objects.create(title='Моята чернова', hook='h', content='c', published=False, author=self.admin)
        SiteSettings.objects.update_or_create(
            pk=1, defaults={'ai_tasks_last_run_at': timezone.now() - timedelta(hours=2)},
        )
        self.client.force_login(self.admin)

        response = self.client.get(reverse('admin:index'))

        self.assertContains(response, 'Моите чернови')
        self.assertContains(response, 'Моята чернова')
        self.assertContains(response, 'Състояние на системата')
        self.assertContains(response, 'Таймерът на сървъра може да е спрял.')
        self.assertContains(response, reverse('admin-help'))
        self.assertNotContains(response, 'Администраторско ръководство')

    def test_help_page_requires_staff(self):
        response = self.client.get(reverse('admin-help'))
        self.assertEqual(response.status_code, 302)
        self.client.force_login(self.admin)
        response = self.client.get(reverse('admin-help'))
        self.assertContains(response, 'ИИ чернови за седмичната анкета')

    def test_login_page_is_in_bulgarian(self):
        response = self.client.get(reverse('admin:login'))
        self.assertContains(response, 'Добре дошли отново в')
        self.assertNotContains(response, 'Welcome back to')
