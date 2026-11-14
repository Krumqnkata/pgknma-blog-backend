from datetime import date, timedelta
from importlib import import_module

from django.apps import apps as django_apps
from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APITestCase

from .meme_weeks import finalize_meme_weeks
from .models import (
    GamificationPointRule,
    MemeOfWeek,
    MemeWeekResult,
    Notification,
    NotificationPreference,
    PointTransaction,
    SiteSettings,
    meme_week_start,
)
from .notification_categories import CONTENT_MEME


def make_meme(user, title, *, week, votes=0, status='approved', created_days_ago=0):
    meme = MemeOfWeek.objects.create(user=user, title=title, status=status, week_start=week)
    MemeOfWeek.objects.filter(pk=meme.pk).update(
        votes=votes,
        created_at=timezone.now() - timedelta(days=created_days_ago),
    )
    meme.refresh_from_db()
    return meme


class MemeWeekBoundaryTests(TestCase):
    def test_week_starts_on_monday(self):
        self.assertEqual(meme_week_start(date(2026, 11, 16)), date(2026, 11, 16))  # Monday
        self.assertEqual(meme_week_start(date(2026, 11, 15)), date(2026, 11, 9))   # Sunday
        self.assertEqual(meme_week_start(date(2026, 11, 12)), date(2026, 11, 9))

    def test_meme_joins_the_week_in_which_it_is_approved(self):
        user = User.objects.create_user('автор', password='x-Pass-123456')
        meme = MemeOfWeek.objects.create(user=user, title='Чакащо', status='pending')
        self.assertIsNone(meme.week_start)

        meme.status = 'approved'
        meme.save(update_fields=('status',))  # like the admin approve action
        meme.refresh_from_db()
        self.assertEqual(meme.week_start, meme_week_start())


class MemeWeekApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user('гласуващ', password='x-Pass-123456')
        self.author = User.objects.create_user('автор', password='x-Pass-123456')
        self.current = meme_week_start()
        self.previous = self.current - timedelta(days=7)
        self.now_meme = make_meme(self.author, 'Тази седмица', week=self.current, votes=2)
        self.old_meme = make_meme(self.author, 'Миналата седмица', week=self.previous, votes=9)

    def test_list_shows_only_the_running_week_by_default(self):
        data = self.client.get('/api/memes/').json()
        titles = [meme['title'] for meme in data]
        self.assertEqual(titles, ['Тази седмица'])
        self.assertTrue(data[0]['is_current_week'])
        self.assertEqual(data[0]['week_start'], self.current.isoformat())

    def test_past_week_can_be_listed_but_not_voted(self):
        data = self.client.get('/api/memes/', {'week': self.previous.isoformat()}).json()
        self.assertEqual([meme['title'] for meme in data], ['Миналата седмица'])
        self.assertFalse(data[0]['is_current_week'])
        self.assertEqual(self.client.get('/api/memes/', {'week': 'вчера'}).status_code, 400)

        self.client.force_authenticate(self.user)
        closed = self.client.post(f'/api/memes/{self.old_meme.pk}/vote/')
        self.assertEqual(closed.status_code, 400)
        self.assertEqual(closed.json()['code'], 'meme_week_closed')
        opened = self.client.post(f'/api/memes/{self.now_meme.pk}/vote/')
        self.assertEqual(opened.status_code, 200)
        # The vote count is recalculated from the real voters.
        self.assertEqual(opened.json()['votes'], 1)
        self.assertTrue(opened.json()['has_voted'])

    def test_weeks_archive_and_latest_winner(self):
        self.assertEqual(self.client.get('/api/memes/winner/').json(), {'winner': None})
        finalize_meme_weeks()

        winner = self.client.get('/api/memes/winner/').json()
        self.assertEqual(winner['winner']['title'], 'Миналата седмица')
        self.assertEqual(winner['week_start'], self.previous.isoformat())

        archive = self.client.get('/api/memes/weeks/').json()
        self.assertEqual(archive['current_week_start'], self.current.isoformat())
        self.assertEqual(archive['results'][0]['winner']['title'], 'Миналата седмица')
        self.assertEqual(archive['results'][0]['meme_count'], 1)
        self.assertFalse(archive['has_more'])

    def test_disabled_feature_hides_everything(self):
        site_settings, _ = SiteSettings.objects.get_or_create(pk=1)
        site_settings.enable_meme_of_the_week = False
        site_settings.save()
        self.assertEqual(self.client.get('/api/memes/').json(), [])
        self.assertEqual(self.client.get('/api/memes/winner/').json(), {'winner': None})
        self.assertEqual(finalize_meme_weeks(), [])


class FinalizeMemeWeeksTests(TestCase):
    def setUp(self):
        self.author = User.objects.create_user('автор', password='x-Pass-123456')
        self.other = User.objects.create_user('друг', password='x-Pass-123456')
        self.current = meme_week_start()
        self.previous = self.current - timedelta(days=7)

    def test_winner_gets_points_and_everyone_is_notified_once(self):
        make_meme(self.other, 'Второ', week=self.previous, votes=3)
        winner = make_meme(self.author, 'Първо', week=self.previous, votes=7)
        make_meme(self.other, 'Текущо', week=self.current, votes=99)

        results = finalize_meme_weeks()
        self.assertEqual(len(results), 1)
        result = MemeWeekResult.objects.get(week_start=self.previous)
        self.assertEqual(result.winner, winner)
        self.assertEqual((result.winner_votes, result.meme_count), (7, 2))
        self.assertIsNotNone(result.announced_at)
        self.assertIsNotNone(result.points_awarded_at)

        bonus = PointTransaction.objects.get(user=self.author, action='meme_week_winner')
        self.assertEqual(bonus.points, 50)
        announcement = Notification.objects.get(category=CONTENT_MEME)
        self.assertIn('„Първо“ от автор с 7 гласа', announcement.text)
        self.assertEqual(announcement.action_url, '/meme-of-the-week')
        self.assertIsNone(announcement.user)

        # Running again changes nothing.
        self.assertEqual(finalize_meme_weeks(), [])
        self.assertEqual(PointTransaction.objects.filter(action='meme_week_winner').count(), 1)
        self.assertEqual(Notification.objects.filter(category=CONTENT_MEME).count(), 1)
        self.assertFalse(MemeWeekResult.objects.filter(week_start=self.current).exists())

    def test_tie_goes_to_the_meme_uploaded_first(self):
        make_meme(self.other, 'По-късно', week=self.previous, votes=5, created_days_ago=1)
        first = make_meme(self.author, 'По-рано', week=self.previous, votes=5, created_days_ago=3)
        finalize_meme_weeks()
        self.assertEqual(MemeWeekResult.objects.get(week_start=self.previous).winner, first)

    def test_points_come_from_the_admin_rule(self):
        GamificationPointRule.objects.update_or_create(
            action='meme_week_winner', defaults={'points': 75, 'active': True},
        )
        make_meme(self.author, 'Меме', week=self.previous, votes=1)
        finalize_meme_weeks()
        self.assertEqual(PointTransaction.objects.get(action='meme_week_winner').points, 75)

    def test_older_missed_weeks_close_quietly(self):
        older = self.previous - timedelta(days=14)
        make_meme(self.author, 'Старо', week=older, votes=4)
        finalize_meme_weeks()
        result = MemeWeekResult.objects.get(week_start=older)
        self.assertIsNone(result.announced_at)
        self.assertFalse(PointTransaction.objects.filter(action='meme_week_winner').exists())
        self.assertFalse(Notification.objects.filter(category=CONTENT_MEME).exists())

    def test_pending_and_rejected_memes_do_not_count(self):
        make_meme(self.author, 'Отхвърлено', week=self.previous, votes=50, status='rejected')
        winner = make_meme(self.other, 'Одобрено', week=self.previous, votes=1)
        finalize_meme_weeks()
        self.assertEqual(MemeWeekResult.objects.get(week_start=self.previous).winner, winner)


def _items(data):
    return data['results'] if isinstance(data, dict) else data


class MemeNotificationPreferenceTests(APITestCase):
    def test_users_can_switch_off_meme_announcements(self):
        user = User.objects.create_user('читател', password='x-Pass-123456')
        author = User.objects.create_user('автор', password='x-Pass-123456')
        make_meme(author, 'Меме', week=meme_week_start() - timedelta(days=7), votes=2)
        finalize_meme_weeks()

        self.client.force_authenticate(user)
        texts = [item['text'] for item in _items(self.client.get('/api/notifications/').json())]
        self.assertTrue(any('Меме на седмицата' in text for text in texts))

        NotificationPreference.objects.update_or_create(user=user, defaults={'memes_enabled': False})
        texts = [item['text'] for item in _items(self.client.get('/api/notifications/').json())]
        self.assertFalse(any('Меме на седмицата' in text for text in texts))


class BackfillMigrationTests(TestCase):
    def test_existing_memes_get_weeks_and_old_weeks_close_silently(self):
        author = User.objects.create_user('автор', password='x-Pass-123456')
        old = MemeOfWeek.objects.create(user=author, title='Старо', status='pending')
        MemeOfWeek.objects.filter(pk=old.pk).update(
            status='approved', week_start=None, votes=3,
            created_at=timezone.now() - timedelta(days=21),
        )
        fresh = MemeOfWeek.objects.create(user=author, title='Ново', status='pending')
        MemeOfWeek.objects.filter(pk=fresh.pk).update(status='approved', week_start=None)

        migration = import_module('blog.migrations.0083_backfill_meme_weeks')
        migration.backfill_meme_weeks(django_apps, None)

        old.refresh_from_db()
        fresh.refresh_from_db()
        self.assertEqual(fresh.week_start, meme_week_start())
        self.assertLess(old.week_start, meme_week_start())
        result = MemeWeekResult.objects.get(week_start=old.week_start)
        self.assertEqual(result.winner, old)
        self.assertIsNone(result.announced_at)
        self.assertFalse(Notification.objects.filter(category=CONTENT_MEME).exists())
        self.assertEqual(finalize_meme_weeks(), [])
