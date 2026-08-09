from datetime import timedelta

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from .gamification.services import (
    award_badge,
    change_points,
    ensure_current_weekly_mission,
    level_state,
    record_mission_activity,
    remove_mission_activity,
)
from .models import (
    Badge,
    BellSongSuggestion,
    Category,
    Comments,
    DigitalReward,
    MemeOfWeek,
    Notification,
    PointTransaction,
    PollAnswer,
    PollOption,
    PollQuestion,
    Posts,
    UserBadge,
    UserDigitalReward,
    UserMissionProgress,
)


class GamificationTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='game-student',
            password='Strong-password-2026',
        )
        category = Category.objects.create(
            full_name='Новини',
            short_name='game-news',
        )
        self.post = Posts.objects.create(
            title='Геймификация',
            category=category,
            hook='Кратко описание',
            content='Съдържание',
            published=True,
            allowed=True,
        )

    def points(self):
        self.user.userprofile.refresh_from_db()
        return self.user.userprofile.gamification_points

    def test_comment_awards_once_and_deletion_reverses_points(self):
        comment = Comments.objects.create(
            user=self.user,
            post=self.post,
            content='Полезен коментар',
        )
        self.assertEqual(self.points(), 5)

        comment.content = 'Редактиран коментар'
        comment.save()
        self.assertEqual(self.points(), 5)
        self.assertEqual(
            PointTransaction.objects.filter(action='comment', points__gt=0).count(),
            1,
        )

        comment.delete()
        self.assertEqual(self.points(), 0)
        self.assertTrue(
            PointTransaction.objects.filter(
                action='comment_reversed', points=-5
            ).exists()
        )

    def test_only_five_comments_per_day_receive_points(self):
        for number in range(6):
            Comments.objects.create(
                user=self.user,
                post=self.post,
                content=f'Коментар {number}',
            )
        self.assertEqual(self.points(), 25)
        self.assertEqual(
            PointTransaction.objects.filter(
                user=self.user, action='comment', points__gt=0
            ).count(),
            5,
        )

    def test_poll_answer_is_unique_and_awards_once(self):
        question = PollQuestion.objects.create(
            title='Анкета',
            start_date=timezone.now(),
            end_date=timezone.now() + timedelta(days=1),
        )
        option = PollOption.objects.create(
            question=question,
            text='Отговор',
            key='a',
        )
        PollAnswer.objects.create(
            user=self.user,
            question=question,
            selected_option=option,
        )
        self.assertEqual(self.points(), 5)
        with self.assertRaises((IntegrityError, ValidationError)), transaction.atomic():
            PollAnswer.objects.create(
                user=self.user,
                question=question,
                selected_option=option,
            )
        self.assertEqual(self.points(), 5)

    def test_approval_transitions_are_reversible_without_duplicate_rewards(self):
        suggestion = BellSongSuggestion.objects.create(
            user=self.user,
            title='Песен',
            link='https://example.com/song',
        )
        suggestion.status = 'approved'
        suggestion.save(update_fields=('status',))
        self.assertEqual(self.points(), 20)

        suggestion.save(update_fields=('status',))
        self.assertEqual(self.points(), 20)

        suggestion.status = 'rejected'
        suggestion.save(update_fields=('status',))
        self.assertEqual(self.points(), 0)

        suggestion.status = 'approved'
        suggestion.save(update_fields=('status',))
        self.assertEqual(self.points(), 20)
        self.assertEqual(
            PointTransaction.objects.filter(
                user=self.user, action='song_approved', points__gt=0
            ).count(),
            2,
        )

    def test_approved_meme_awards_points_and_badge_then_reverses(self):
        meme = MemeOfWeek.objects.create(
            user=self.user,
            title='Училищно меме',
            status='approved',
        )
        self.assertEqual(self.points(), 20)
        self.assertTrue(
            UserBadge.objects.filter(
                user=self.user,
                badge__slug='meme-master',
            ).exists()
        )
        meme.status = 'rejected'
        meme.save(update_fields=('status',))
        self.assertEqual(self.points(), 0)

    def test_badge_is_awarded_automatically_only_once(self):
        first = Comments.objects.create(
            user=self.user,
            post=self.post,
            content='Първи',
        )
        Comments.objects.create(
            user=self.user,
            post=self.post,
            content='Втори',
        )
        badge = Badge.objects.get(slug='first-comment')
        self.assertEqual(
            UserBadge.objects.filter(user=self.user, badge=badge).count(),
            1,
        )
        first.delete()
        self.assertEqual(
            UserBadge.objects.filter(user=self.user, badge=badge).count(),
            1,
        )

    def test_levels_and_max_level_progress(self):
        state = level_state(250)
        self.assertEqual(state.current.title, 'Познато лице')
        self.assertEqual(state.next.title, 'Дигитален посланик')
        self.assertEqual(state.points_to_next, 250)

        state = level_state(720)
        self.assertEqual(state.progress_percent, 72)

        state = level_state(1200)
        self.assertEqual(state.current.title, 'Легенда на ПГКНМА')
        self.assertIsNone(state.next)
        self.assertEqual(state.progress_percent, 100)
        self.assertEqual(state.points_to_next, 0)

    def test_profile_api_is_private_and_returns_progress(self):
        client = APIClient()
        anonymous = client.get(reverse('gamification-profile'))
        self.assertIn(anonymous.status_code, (401, 403))

        change_points(
            user=self.user,
            points=100,
            action='admin_adjustment',
            description='Тестова корекция',
        )
        client.force_authenticate(self.user)
        response = client.get(reverse('gamification-profile'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['points'], 100)
        self.assertEqual(response.data['level']['title'], 'Активен ученик')
        self.assertFalse(response.data['leaderboard_opt_in'])
        self.assertTrue(response.data['recent_transactions'])

    def test_private_notifications_are_not_leaked(self):
        other = User.objects.create_user(username='other-game-user')
        Notification.objects.create(user=self.user, text='Лична награда')
        Notification.objects.create(user=other, text='Чужда награда')
        Notification.objects.create(text='Общо съобщение')

        client = APIClient()
        public_texts = [item['text'] for item in client.get(reverse('notification-list')).data]
        self.assertEqual(public_texts, ['Общо съобщение'])

        client.force_authenticate(self.user)
        user_texts = [item['text'] for item in client.get(reverse('notification-list')).data]
        self.assertIn('Лична награда', user_texts)
        self.assertIn('Общо съобщение', user_texts)
        self.assertNotIn('Чужда награда', user_texts)

    def test_balance_never_falls_below_zero(self):
        point_transaction, created = change_points(
            user=self.user,
            points=-50,
            action='admin_adjustment',
            description='Корекция',
        )
        self.assertFalse(created)
        self.assertIsNone(point_transaction)
        self.assertEqual(self.points(), 0)

        change_points(
            user=self.user,
            points=20,
            action='admin_adjustment',
            description='Добавяне',
        )
        change_points(
            user=self.user,
            points=-50,
            action='admin_adjustment',
            description='Отнемане',
        )
        ledger_total = PointTransaction.objects.filter(
            user=self.user
        ).aggregate(total=Sum('points'))['total']
        self.assertEqual(ledger_total, self.points())
        self.assertEqual(self.points(), 0)

    def test_anonymous_user_cannot_run_admin_point_adjustment(self):
        response = APIClient().post(
            reverse('admin:blog_userprofile_changelist'),
            {
                'action': 'adjust_points',
                '_selected_action': [self.user.userprofile.pk],
                'apply': '1',
                'points': '100',
                'reason': 'Неоторизирана промяна',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response['Location'])
        self.assertEqual(self.points(), 0)

    def test_weekly_mission_tracks_each_goal_and_rewards_once(self):
        mission = ensure_current_weekly_mission()
        for post_id in (101, 102, 103):
            record_mission_activity(self.user, 'post_read', post_id)
        record_mission_activity(self.user, 'poll_vote', 201)
        record_mission_activity(self.user, 'calendar_visit', '2026-08-09')
        record_mission_activity(self.user, 'comment', 301)

        progress = UserMissionProgress.objects.get(
            user=self.user,
            mission=mission,
        )
        self.assertIsNotNone(progress.completed_at)
        self.assertIsNotNone(progress.rewarded_at)
        self.assertEqual(self.points(), 50)

        record_mission_activity(self.user, 'comment', 301)
        self.assertEqual(self.points(), 50)
        self.assertEqual(
            PointTransaction.objects.filter(
                user=self.user,
                action='mission_completed',
            ).count(),
            1,
        )

        remove_mission_activity(self.user, 'comment', 301)
        self.assertEqual(self.points(), 0)
        progress.refresh_from_db()
        self.assertIsNone(progress.completed_at)

        record_mission_activity(self.user, 'comment', 303)
        self.assertEqual(self.points(), 50)
        self.assertEqual(
            PointTransaction.objects.filter(
                user=self.user,
                action='mission_completed',
                points__gt=0,
            ).count(),
            2,
        )

    def test_post_read_endpoint_requires_real_post_and_minimum_dwell(self):
        client = APIClient()
        client.force_authenticate(self.user)
        too_soon = client.post(
            reverse('gamification-activity'),
            {'action': 'post_read', 'object_id': self.post.pk, 'engagement_seconds': 5},
            format='json',
        )
        self.assertEqual(too_soon.status_code, 400)

        accepted = client.post(
            reverse('gamification-activity'),
            {'action': 'post_read', 'object_id': self.post.pk, 'engagement_seconds': 15},
            format='json',
        )
        self.assertEqual(accepted.status_code, 202)
        repeated = client.post(
            reverse('gamification-activity'),
            {'action': 'post_read', 'object_id': self.post.pk, 'engagement_seconds': 30},
            format='json',
        )
        self.assertEqual(repeated.status_code, 202)
        progress = UserMissionProgress.objects.get(
            user=self.user,
            mission=ensure_current_weekly_mission(),
        )
        self.assertEqual(progress.progress['read_posts'], 1)

    def test_leaderboard_is_opt_in_and_preference_is_private(self):
        other = User.objects.create_user(username='leader-student')
        change_points(
            user=self.user,
            points=40,
            action='admin_adjustment',
            description='Точки',
        )
        change_points(
            user=other,
            points=80,
            action='admin_adjustment',
            description='Точки',
        )
        other.userprofile.leaderboard_opt_in = True
        other.userprofile.save(update_fields=('leaderboard_opt_in',))

        client = APIClient()
        public_response = client.get(reverse('gamification-leaderboard'))
        usernames = [
            row['username'] for row in public_response.data['all_time']['top']
        ]
        self.assertEqual(usernames, ['leader-student'])

        client.force_authenticate(self.user)
        preference = client.patch(
            reverse('gamification-preferences'),
            {'leaderboard_opt_in': True},
            format='json',
        )
        self.assertEqual(preference.status_code, 200)
        ranked = client.get(reverse('gamification-leaderboard'))
        usernames = [row['username'] for row in ranked.data['all_time']['top']]
        self.assertEqual(usernames, ['leader-student', 'game-student'])
        self.assertEqual(
            ranked.data['all_time']['current_user']['username'],
            'game-student',
        )

    def test_badge_can_unlock_configured_digital_reward(self):
        reward = DigitalReward.objects.create(
            key='emerald-frame',
            title='Изумрудена рамка',
            kind=DigitalReward.Kind.AVATAR_FRAME,
        )
        badge = Badge.objects.get(slug='event-participant')
        badge.digital_rewards.add(reward)
        award_badge(self.user, badge, reason='Участие в събитие')
        self.assertTrue(
            UserDigitalReward.objects.filter(
                user=self.user,
                reward=reward,
                source_badge=badge,
            ).exists()
        )
