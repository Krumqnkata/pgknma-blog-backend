from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from .gamification.config import daily_limit, point_value
from .models import Badge, DigitalReward, GamificationPointRule, SiteSettings


class GamificationCatalogTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = reverse('gamification-catalog')
        self.site, _ = SiteSettings.objects.get_or_create(pk=1)
        self.site.maintenance_mode = False
        self.site.enable_weekly_poll = True
        self.site.enable_bell_suggestions = True
        self.site.enable_meme_of_the_week = True
        self.site.save()
        # Seed migrations contain defaults; each test controls its catalogue.
        Badge.objects.all().delete()
        DigitalReward.objects.all().delete()
        GamificationPointRule.objects.all().delete()

    def badge(self, slug, **kwargs):
        return Badge.objects.create(slug=slug, title=slug, description='Описание', **kwargs)

    def reward(self, key, badge, **kwargs):
        reward = DigitalReward.objects.create(key=key, title=key, kind='avatar_frame', **kwargs)
        badge.digital_rewards.add(reward)
        return reward

    def test_public_rules_use_the_same_live_settings_as_awarding(self):
        rule = GamificationPointRule.objects.create(action='comment', points=13, daily_limit=3)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        rules = {row['action']: row for row in response.data['point_rules']}
        self.assertEqual(rules['comment']['points'], point_value('comment'))
        self.assertEqual(rules['comment']['daily_limit'], daily_limit('comment'))
        self.assertEqual(rules['comment']['points'], 13)
        rule.points, rule.daily_limit = 21, None
        rule.save()
        updated = {row['action']: row for row in self.client.get(self.url).data['point_rules']}
        self.assertEqual(updated['comment']['points'], 21)
        self.assertIsNone(updated['comment']['daily_limit'])

    @override_settings(GAMIFICATION_POINTS={'post_read': 9}, GAMIFICATION_COMMENT_DAILY_LIMIT=7)
    def test_fallback_settings_and_disabled_rules_remain_accurate(self):
        GamificationPointRule.objects.create(action='comment', points=10, active=False)
        GamificationPointRule.objects.create(action='post_read', points=9, daily_limit=0)
        GamificationPointRule.objects.create(action='calendar_visit', points=0, daily_limit=4)
        self.site.enable_weekly_poll = False
        self.site.save()
        rules = {row['action']: row for row in self.client.get(self.url).data['point_rules']}
        for action in ('comment', 'post_read', 'calendar_visit', 'poll_vote'):
            self.assertFalse(rules[action]['active'])
        self.assertEqual(rules['comment']['points'], 0)
        self.assertEqual(rules['comment']['daily_limit'], 0)
        GamificationPointRule.objects.all().delete()
        fallback = {row['action']: row for row in self.client.get(self.url).data['point_rules']}
        self.assertEqual(fallback['post_read']['points'], 9)
        self.assertEqual(fallback['comment']['daily_limit'], 7)
        self.assertIsNone(fallback['song_approved']['daily_limit'])

    def test_catalog_hides_secret_inactive_future_and_expired_unlock_paths(self):
        visible = self.badge('visible', requirement={'type': 'total_points', 'target': 250})
        now = timezone.now()
        for slug, options in (
            ('secret', {'hidden': True}), ('inactive', {'active': False}),
            ('future', {'starts_at': now + timedelta(days=1)}),
            ('expired', {'ends_at': now - timedelta(days=1)}),
        ):
            badge = self.badge(slug, **options)
            self.reward(f'{slug}-reward', badge)
        shared = self.reward('visible-reward', visible, config={'color': '#123456', 'internal_note': 'private'})
        Badge.objects.get(slug='secret').digital_rewards.add(shared)
        self.reward('disabled-reward', visible, active=False)
        data = self.client.get(self.url).data
        self.assertEqual([badge['slug'] for badge in data['badges']], ['visible'])
        self.assertEqual([reward['key'] for reward in data['rewards']], ['visible-reward'])
        self.assertEqual(data['rewards'][0]['config'], {'color': '#123456'})
        self.assertEqual([badge['slug'] for badge in data['rewards'][0]['badges']], ['visible'])
        self.assertEqual(data['badges'][0]['condition'], 'Достигни общо 250 точки.')
        self.assertNotIn('earned', data['badges'][0])

    def test_conditions_describe_current_and_legacy_requirements(self):
        requirements = {
            'actions': ({'type': 'action_count', 'action': 'post_read', 'target': 7}, '7 действия с начислени точки'),
            'legacy-actions': ({'action': 'comment', 'count': 3}, '3 действия с начислени точки'),
            'days': ({'type': 'active_days', 'target': 5}, '5 различни дни'),
            'legacy-days': ({'activity_days': 4}, '4 различни дни'),
            'manual': ({'manual_or_event': True}, 'Присъжда се от екипа'),
        }
        for slug, (requirement, _) in requirements.items():
            self.badge(slug, requirement=requirement)
        data = self.client.get(self.url).data
        for badge in data['badges']:
            self.assertIn(requirements[badge['slug']][1], badge['condition'])

    def test_maintenance_is_respected_and_endpoint_is_read_only(self):
        self.assertEqual(self.client.post(self.url, {}, format='json').status_code, 405)
        self.site.maintenance_mode = True
        self.site.save()
        self.assertEqual(self.client.get(self.url).status_code, 503)
