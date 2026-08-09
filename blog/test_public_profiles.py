from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from .gamification.services import award_badge, change_points
from .models import (
    Badge,
    Category,
    Comments,
    DigitalReward,
    Posts,
    UserDigitalReward,
)


class PublicUserProfileTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='Profile.Student',
            email='student@example.com',
            password='Strong-profile-password-2026',
        )
        self.other = User.objects.create_user(
            username='profile-viewer',
            email='viewer@example.com',
        )
        self.client = APIClient()
        self.url = reverse(
            'public-user-profile',
            kwargs={'username': self.user.username},
        )
        self.settings_url = reverse('profile-public-settings')
        category = Category.objects.create(
            full_name='Профилни тестове',
            short_name='profile-tests',
        )
        self.public_post = Posts.objects.create(
            title='Публична публикация',
            category=category,
            hook='Описание',
            content='Съдържание',
            published=True,
            allowed=True,
        )
        self.private_post = Posts.objects.create(
            title='Чернова',
            category=category,
            hook='Описание',
            content='Съдържание',
            published=False,
            allowed=False,
        )

    def test_public_profile_hides_email_by_default_but_owner_can_see_it(self):
        public_response = self.client.get(self.url)
        self.assertEqual(public_response.status_code, 200)
        self.assertIsNone(public_response.data['user']['email'])
        self.assertFalse(public_response.data['is_owner'])

        self.client.force_authenticate(self.user)
        owner_response = self.client.get(self.url)
        self.assertEqual(owner_response.status_code, 200)
        self.assertEqual(
            owner_response.data['user']['email'],
            'student@example.com',
        )
        self.assertTrue(owner_response.data['is_owner'])

    def test_public_profile_exposes_editorial_role(self):
        regular_response = self.client.get(self.url)
        self.assertIsNone(regular_response.data['user']['site_role'])

        self.user.is_staff = True
        self.user.save(update_fields=('is_staff',))
        contributor_response = self.client.get(self.url)
        self.assertEqual(
            contributor_response.data['user']['site_role'],
            'contributor',
        )

        self.user.is_superuser = True
        self.user.save(update_fields=('is_superuser',))
        administrator_response = self.client.get(self.url)
        self.assertEqual(
            administrator_response.data['user']['site_role'],
            'administrator',
        )

    def test_owner_can_update_profile_and_explicitly_publish_email(self):
        anonymous = self.client.patch(
            self.settings_url,
            {'show_email_on_profile': True},
            format='json',
        )
        self.assertIn(anonymous.status_code, (401, 403))

        self.client.force_authenticate(self.user)
        updated = self.client.patch(
            self.settings_url,
            {
                'bio': '  Участвам в училищния блог.  ',
                'show_email_on_profile': True,
            },
            format='json',
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.data['bio'], 'Участвам в училищния блог.')

        self.client.force_authenticate(user=None)
        public_response = self.client.get(self.url)
        self.assertEqual(
            public_response.data['user']['email'],
            'student@example.com',
        )
        self.assertEqual(
            public_response.data['bio'],
            'Участвам в училищния блог.',
        )

    def test_hidden_profile_returns_404_to_others_but_remains_visible_to_owner(self):
        profile = self.user.userprofile
        profile.public_profile_enabled = False
        profile.save(update_fields=('public_profile_enabled',))

        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_only_comments_on_public_posts_are_exposed(self):
        visible = Comments.objects.create(
            user=self.user,
            post=self.public_post,
            content='Коментар към публичната публикация',
        )
        Comments.objects.create(
            user=self.user,
            post=self.private_post,
            content='Коментар към скритата публикация',
        )

        response = self.client.get(self.url)
        self.assertEqual(response.data['stats']['comments'], 1)
        self.assertEqual(len(response.data['recent_comments']), 1)
        self.assertEqual(response.data['recent_comments'][0]['id'], visible.pk)
        self.assertEqual(
            response.data['recent_comments'][0]['post_title'],
            'Публична публикация',
        )

    def test_comment_visibility_setting_hides_list_and_public_count(self):
        Comments.objects.create(
            user=self.user,
            post=self.public_post,
            content='Публичен коментар',
        )
        profile = self.user.userprofile
        profile.show_comments_on_profile = False
        profile.save(update_fields=('show_comments_on_profile',))

        response = self.client.get(self.url)
        self.assertFalse(response.data['comments_visible'])
        self.assertEqual(response.data['recent_comments'], [])
        self.assertEqual(response.data['stats']['comments'], 0)

    def test_profile_contains_level_badges_and_digital_rewards(self):
        change_points(
            user=self.user,
            points=120,
            action='admin_adjustment',
            description='Профилен тест',
        )
        reward = DigitalReward.objects.create(
            key='profile-test-frame',
            title='Тестова рамка',
            kind=DigitalReward.Kind.AVATAR_FRAME,
        )
        badge = Badge.objects.get(slug='event-participant')
        badge.digital_rewards.add(reward)
        award_badge(self.user, badge, reason='Тестово участие')

        response = self.client.get(self.url)
        self.assertEqual(response.data['points'], 120)
        self.assertEqual(response.data['level']['title'], 'Активен ученик')
        self.assertEqual(response.data['badges'][0]['slug'], 'event-participant')
        self.assertEqual(response.data['rewards'][0]['key'], 'profile-test-frame')
        self.assertEqual(response.data['stats']['badges'], 1)
        self.assertEqual(response.data['stats']['rewards'], 1)
        self.assertTrue(
            UserDigitalReward.objects.filter(
                user=self.user,
                reward=reward,
            ).exists()
        )

    def test_inactive_and_unknown_accounts_do_not_have_public_profiles(self):
        self.user.is_active = False
        self.user.save(update_fields=('is_active',))
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(
            self.client.get(
                reverse(
                    'public-user-profile',
                    kwargs={'username': 'missing-user'},
                )
            ).status_code,
            404,
        )

    def test_current_profile_response_contains_privacy_settings(self):
        self.client.force_authenticate(self.user)
        response = self.client.get(reverse('profile-current'))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['public_profile']['public_profile_enabled'])
        self.assertFalse(response.data['public_profile']['show_email_on_profile'])
        self.assertTrue(response.data['public_profile']['show_comments_on_profile'])

    def test_bio_length_is_validated(self):
        self.client.force_authenticate(self.user)
        response = self.client.patch(
            self.settings_url,
            {'bio': 'x' * 501},
            format='json',
        )
        self.assertEqual(response.status_code, 400)
