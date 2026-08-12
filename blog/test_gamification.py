import json
from contextlib import redirect_stdout
from datetime import timedelta
from io import StringIO

from django.contrib import admin
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.test import RequestFactory, TestCase
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
from .forms import BadgeAdminForm, DigitalRewardAdminForm, MissionAdminForm
from .models import (
    Badge,
    BellSongSuggestion,
    Category,
    Comments,
    DigitalReward,
    GamificationPointRule,
    GamificationNotification,
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


class GamificationAdminFormTests(TestCase):
    def test_badge_condition_is_stored_as_readable_structured_data(self):
        form = BadgeAdminForm(data={
            'slug': 'helpful-reader',
            'title': 'Полезен читател',
            'description': 'За редовно четене.',
            'icon': 'book-open',
            'rarity': Badge.Rarity.RARE,
            'condition_type': 'action_count',
            'condition_action': 'post_read',
            'condition_target': 7,
            'active': True,
            'points_reward': 10,
        })

        self.assertTrue(form.is_valid(), form.errors.as_json())
        self.assertEqual(form.cleaned_data['requirement'], {
            'type': 'action_count',
            'action': 'post_read',
            'target': 7,
        })

    def test_digital_reward_visual_settings_are_stored_without_raw_json(self):
        form = DigitalRewardAdminForm(data={
            'key': 'violet-title',
            'title': 'Виолетова титла',
            'kind': DigitalReward.Kind.USER_TITLE,
            'active': True,
            'visual_color': '#8b5cf6',
            'secondary_color': '#22d3ee',
            'effect_style': 'gradient',
            'display_text': 'Училищен посланик',
            'display_icon': 'sparkles',
        })

        self.assertTrue(form.is_valid(), form.errors.as_json())
        self.assertEqual(form.cleaned_data['config']['text'], 'Училищен посланик')
        self.assertEqual(form.cleaned_data['config']['style'], 'gradient')


class MissionAdminFormTests(TestCase):
    def form_data(self, requirements):
        start = timezone.now() + timedelta(days=1)
        return {
            'slug': 'readable-mission',
            'title': 'Четима мисия',
            'description': 'Описание',
            'start_date': start,
            'end_date': start + timedelta(days=7),
            'reward_points': 50,
            'active': True,
            'requirements': json.dumps(requirements, ensure_ascii=False),
        }

    def test_form_accepts_and_normalizes_readable_requirements(self):
        requirements = [
            {
                'key': 'read_posts',
                'label': 'Прочети 3 различни публикации',
                'action': 'post_read',
                'target': '3',
            },
            {
                'key': 'comment',
                'label': 'Остави коментар',
                'action': 'comment',
                'target': 1,
            },
        ]
        form = MissionAdminForm(data=self.form_data(requirements))

        self.assertTrue(form.is_valid(), form.errors.as_json())
        self.assertEqual(form.cleaned_data['requirements'][0]['target'], 3)
        self.assertEqual(
            form.cleaned_data['requirements'][0]['label'],
            'Прочети 3 различни публикации',
        )

    def test_form_rejects_unsupported_action(self):
        requirements = [{
            'key': 'unknown_goal',
            'label': 'Неподдържана цел',
            'action': 'unknown_action',
            'target': 1,
        }]
        form = MissionAdminForm(data=self.form_data(requirements))

        self.assertFalse(form.is_valid())
        self.assertIn('изберете поддържано действие', form.errors['requirements'][0])

    def test_form_rejects_duplicate_system_keys(self):
        requirements = [
            {
                'key': 'comment',
                'label': 'Първи коментар',
                'action': 'comment',
                'target': 1,
            },
            {
                'key': 'comment',
                'label': 'Още коментари',
                'action': 'comment',
                'target': 3,
            },
        ]
        form = MissionAdminForm(data=self.form_data(requirements))

        self.assertFalse(form.is_valid())
        self.assertIn('се повтаря', form.errors['requirements'][0])

    def test_widget_loads_visual_editor_assets(self):
        form = MissionAdminForm()

        self.assertEqual(form['requirements'].value(), '[]')
        self.assertEqual(
            form.fields['requirements'].prepare_value(None),
            '[]',
        )
        self.assertEqual(
            form.fields['requirements'].widget.attrs['data-mission-requirements-input'],
            'true',
        )
        self.assertIn('blog/admin/mission_requirements.js', str(form.media))


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

    def test_point_value_and_daily_limit_are_configurable_from_database(self):
        GamificationPointRule.objects.update_or_create(
            action='comment',
            defaults={'points': 9, 'daily_limit': 1, 'active': True},
        )
        Comments.objects.create(user=self.user, post=self.post, content='Награден')
        Comments.objects.create(user=self.user, post=self.post, content='Ненаграден')

        self.assertEqual(self.points(), 9)
        self.assertEqual(
            PointTransaction.objects.filter(user=self.user, action='comment').count(),
            1,
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

    def test_deleting_old_poll_keeps_points_and_historical_mission_progress(self):
        question = PollQuestion.objects.create(
            title='Стара анкета',
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
        progress = UserMissionProgress.objects.get(
            user=self.user,
            mission=ensure_current_weekly_mission(),
        )
        self.assertEqual(progress.progress['poll_vote'], 1)

        question.delete()

        self.assertEqual(self.points(), 5)
        progress.refresh_from_db()
        self.assertEqual(progress.progress['poll_vote'], 1)
        self.assertFalse(
            PointTransaction.objects.filter(
                user=self.user,
                action='poll_vote_reversed',
            ).exists()
        )

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

    def test_deleting_approved_song_during_cleanup_keeps_points(self):
        suggestion = BellSongSuggestion.objects.create(
            user=self.user,
            title='Архивирана одобрена песен',
            link='https://example.com/archived-song',
        )
        suggestion.status = 'approved'
        suggestion.save(update_fields=('status',))

        suggestion.delete()

        self.assertEqual(self.points(), 20)
        self.assertFalse(
            PointTransaction.objects.filter(
                user=self.user,
                action='song_approved_reversed',
            ).exists()
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

    def test_deleting_approved_meme_during_cleanup_keeps_points(self):
        meme = MemeOfWeek.objects.create(
            user=self.user,
            title='Архивирано одобрено меме',
            status='approved',
        )

        meme.delete()

        self.assertEqual(self.points(), 20)
        self.assertFalse(
            PointTransaction.objects.filter(
                user=self.user,
                action='meme_approved_reversed',
            ).exists()
        )

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
        Notification.objects.all().delete()
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

    def test_gamification_notifications_have_a_separate_channel(self):
        Notification.objects.all().delete()
        Notification.objects.create(text='Общо съобщение', category='general')
        Notification.objects.create(
            user=self.user,
            text='Нова значка',
            category='gamification_badge',
        )
        # Дори погрешно създадено глобално игрово известие не се показва анонимно.
        Notification.objects.create(text='Глобални точки', category='gamification_points')

        client = APIClient()
        anonymous = client.get(reverse('notification-list'))
        self.assertEqual([item['text'] for item in anonymous.data], ['Общо съобщение'])

        client.force_authenticate(self.user)
        general = client.get(reverse('notification-list'), {'channel': 'general'})
        achievements = client.get(
            reverse('notification-list'),
            {'channel': 'gamification'},
        )
        self.assertEqual([item['text'] for item in general.data], ['Общо съобщение'])
        self.assertEqual(
            {item['text'] for item in achievements.data},
            {'Нова значка', 'Глобални точки'},
        )
        self.assertTrue(all(item['is_gamification'] for item in achievements.data))

    def test_admin_keeps_official_and_gamification_notifications_separate(self):
        Notification.objects.all().delete()
        Notification.objects.create(text='Официално', category='general')
        Notification.objects.create(
            user=self.user,
            text='Получени точки',
            category='gamification_points',
        )
        request = RequestFactory().get('/admin/')
        request.user = User.objects.create_superuser(
            username='notification-admin',
            email='notification-admin@example.com',
            password='Strong-password-2026',
        )
        official_admin = admin.site._registry[Notification]
        game_admin = admin.site._registry[GamificationNotification]

        self.assertEqual(
            list(official_admin.get_queryset(request).values_list('text', flat=True)),
            ['Официално'],
        )
        self.assertEqual(
            list(game_admin.get_queryset(request).values_list('text', flat=True)),
            ['Получени точки'],
        )

        official = Notification(text='Ново официално', category='gamification_badge')
        official_admin.save_model(request, official, form=None, change=False)
        self.assertEqual(official.category, 'general')

        self.client.force_login(request.user)
        self.assertEqual(
            self.client.get(reverse('admin:blog_notification_changelist')).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(
                reverse('admin:blog_gamificationnotification_changelist')
            ).status_code,
            200,
        )

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
        self.assertEqual(self.points(), 2)
        self.assertEqual(
            PointTransaction.objects.filter(
                user=self.user,
                action='post_read',
                points__gt=0,
            ).count(),
            1,
        )

    def test_calendar_visit_awards_points_only_once_per_day(self):
        client = APIClient()
        client.force_authenticate(self.user)

        first = client.post(
            reverse('gamification-activity'),
            {'action': 'calendar_visit'},
            format='json',
        )
        repeated = client.post(
            reverse('gamification-activity'),
            {'action': 'calendar_visit'},
            format='json',
        )

        self.assertEqual(first.status_code, 202)
        self.assertEqual(first.data['points_awarded'], 2)
        self.assertEqual(repeated.status_code, 202)
        self.assertEqual(repeated.data['points_awarded'], 0)
        self.assertEqual(self.points(), 2)

    def test_new_badges_use_generic_admin_condition_instead_of_slug_logic(self):
        badge = Badge.objects.create(
            slug='two-helpful-comments',
            title='Два полезни коментара',
            description='Универсално условие',
            requirement={
                'type': 'action_count',
                'action': 'comment',
                'target': 2,
            },
        )

        Comments.objects.create(user=self.user, post=self.post, content='Първи')
        self.assertFalse(UserBadge.objects.filter(user=self.user, badge=badge).exists())
        Comments.objects.create(user=self.user, post=self.post, content='Втори')

        self.assertTrue(UserBadge.objects.filter(user=self.user, badge=badge).exists())

    def test_leaderboard_is_opt_in_and_preference_is_private(self):
        other = User.objects.create_user(username='leader-student')
        other.is_staff = True
        other.save(update_fields=('is_staff',))
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
        self.assertEqual(
            public_response.data['all_time']['top'][0]['site_role'],
            'contributor',
        )

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
        self.assertIsNone(
            ranked.data['all_time']['current_user']['site_role'],
        )

    def test_poll_leaderboard_includes_administrator_role(self):
        question = PollQuestion.objects.create(
            title='Роля в класацията',
            start_date=timezone.now() - timedelta(hours=1),
            end_date=timezone.now() + timedelta(hours=1),
        )
        option = PollOption.objects.create(
            question=question,
            text='Верен отговор',
            key='correct',
            is_correct=True,
        )
        PollAnswer.objects.create(
            user=self.user,
            question=question,
            selected_option=option,
        )
        self.user.is_staff = True
        self.user.is_superuser = True
        self.user.save(update_fields=('is_staff', 'is_superuser'))
        client = APIClient()
        client.force_authenticate(self.user)

        response = client.get(reverse('poll-statistics'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data['leaderboard'][0]['site_role'],
            'administrator',
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

    def test_linking_reward_backfills_users_who_already_earned_badge(self):
        badge = Badge.objects.create(
            slug='legacy-winner',
            title='Стара значка',
            description='Получена преди добавяне на наградата',
            requirement={'type': 'manual'},
        )
        award_badge(self.user, badge, reason='Стара награда')
        reward = DigitalReward.objects.create(
            key='legacy-glow',
            title='Сияние за стара значка',
            kind=DigitalReward.Kind.PROFILE_BORDER,
        )

        badge.digital_rewards.add(reward)

        self.assertTrue(UserDigitalReward.objects.filter(
            user=self.user,
            reward=reward,
            source_badge=badge,
        ).exists())

    def test_owned_rewards_can_be_selected_one_per_kind_and_are_public(self):
        first = DigitalReward.objects.create(
            key='blue-frame',
            title='Синя рамка',
            kind=DigitalReward.Kind.AVATAR_FRAME,
            config={'color': '#2563eb', 'style': 'solid'},
        )
        second = DigitalReward.objects.create(
            key='gold-frame',
            title='Златна рамка',
            kind=DigitalReward.Kind.AVATAR_FRAME,
            config={'color': '#f59e0b', 'style': 'glow'},
        )
        UserDigitalReward.objects.create(user=self.user, reward=first)
        UserDigitalReward.objects.create(user=self.user, reward=second)
        client = APIClient()
        client.force_authenticate(self.user)

        selected_first = client.patch(
            reverse('gamification-reward-select', args=(first.key,)),
            {'selected': True},
            format='json',
        )
        selected_second = client.patch(
            reverse('gamification-reward-select', args=(second.key,)),
            {'selected': True},
            format='json',
        )

        self.assertEqual(selected_first.status_code, 200)
        self.assertEqual(selected_second.status_code, 200)
        self.assertFalse(UserDigitalReward.objects.get(
            user=self.user,
            reward=first,
        ).selected)
        self.assertTrue(UserDigitalReward.objects.get(
            user=self.user,
            reward=second,
        ).selected)

        progress = client.get(reverse('gamification-profile'))
        selected = [row for row in progress.data['rewards'] if row['selected']]
        self.assertEqual([row['key'] for row in selected], ['gold-frame'])

        current_profile = client.get(reverse('profile-current'))
        self.assertEqual(
            current_profile.data['user']['equipped_rewards'][0]['key'],
            'gold-frame',
        )
        public_profile = client.get(
            reverse('public-user-profile', args=(self.user.username,))
        )
        self.assertEqual(
            [row['key'] for row in public_profile.data['rewards'] if row['selected']],
            ['gold-frame'],
        )

    def test_unowned_reward_cannot_be_selected(self):
        reward = DigitalReward.objects.create(
            key='locked-theme',
            title='Заключена тема',
            kind=DigitalReward.Kind.PROFILE_THEME,
        )
        client = APIClient()
        client.force_authenticate(self.user)

        response = client.patch(
            reverse('gamification-reward-select', args=(reward.key,)),
            {'selected': True},
            format='json',
        )

        self.assertEqual(response.status_code, 404)

    def test_official_reward_seed_is_idempotent_and_can_link_badges(self):
        from seed_official_digital_rewards import OFFICIAL_REWARDS, seed_rewards

        with redirect_stdout(StringIO()):
            seed_rewards(link_badges=True, grant_to=None, select=False)
            seed_rewards(link_badges=True, grant_to=None, select=False)

        keys = [definition['key'] for definition in OFFICIAL_REWARDS]
        self.assertEqual(len(keys), 10)
        self.assertEqual(len(set(keys)), 10)
        self.assertEqual(
            DigitalReward.objects.filter(key__in=keys).count(),
            10,
        )
        for reward_kind, _label in DigitalReward.Kind.choices:
            self.assertEqual(
                DigitalReward.objects.filter(
                    key__in=keys,
                    kind=reward_kind,
                ).count(),
                2,
            )
        self.assertEqual(
            sum(
                Badge.objects.get(slug=definition['badge_slug'])
                .digital_rewards.filter(key=definition['key'])
                .count()
                for definition in OFFICIAL_REWARDS
            ),
            10,
        )

    def test_blog_pioneer_badge_is_given_once_to_existing_active_users(self):
        from award_blog_pioneer_badge import (
            BADGE_SLUG,
            REWARD_KEY,
            award_to_users,
            eligible_users,
        )

        second_user = User.objects.create_user(username='second-pioneer')
        inactive_user = User.objects.create_user(
            username='inactive-pioneer',
            is_active=False,
        )
        users = eligible_users(cutoff=timezone.now() + timedelta(minutes=1))
        with redirect_stdout(StringIO()):
            first_result = award_to_users(users)
            second_result = award_to_users(users)

        badge = Badge.objects.get(slug=BADGE_SLUG)
        reward = DigitalReward.objects.get(key=REWARD_KEY)
        self.assertEqual(first_result['awarded'], 2)
        self.assertEqual(second_result['awarded'], 0)
        self.assertEqual(UserBadge.objects.filter(badge=badge).count(), 2)
        self.assertEqual(
            UserDigitalReward.objects.filter(reward=reward).count(),
            2,
        )
        self.assertFalse(
            UserBadge.objects.filter(user=inactive_user, badge=badge).exists()
        )
        self.assertFalse(
            UserDigitalReward.objects.get(user=second_user, reward=reward).selected
        )
        self.assertEqual(self.points(), 0)
        self.assertEqual(
            Notification.objects.filter(category='gamification_badge').count(),
            2,
        )
        self.assertEqual(
            Notification.objects.filter(category='gamification_reward').count(),
            2,
        )

        with redirect_stdout(StringIO()):
            award_to_users(users, select_title=True)
        self.assertTrue(
            UserDigitalReward.objects.get(user=second_user, reward=reward).selected
        )
