"""Public, read-only rules and rewards for the gamification landing page."""

from django.db.models import Prefetch, Q
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from blog.models import Badge, DigitalReward, GamificationLevel, SiteSettings
from blog.permissions import SiteIsAvailable
from blog.serializer import GamificationLevelSerializer
from .config import ACTION_DESCRIPTIONS, daily_limit, point_value


class PointRuleSerializer(serializers.Serializer):
    action = serializers.CharField()
    title = serializers.CharField()
    points = serializers.IntegerField()
    daily_limit = serializers.IntegerField(allow_null=True)
    active = serializers.BooleanField()
    note = serializers.CharField()


class CatalogBadgeSerializer(serializers.Serializer):
    slug = serializers.CharField()
    title = serializers.CharField()
    description = serializers.CharField()
    icon = serializers.CharField()
    rarity = serializers.CharField()
    points_reward = serializers.IntegerField()
    condition = serializers.CharField()
    ends_at = serializers.DateTimeField(allow_null=True)


class CatalogRewardSerializer(serializers.Serializer):
    key = serializers.CharField()
    title = serializers.CharField()
    kind = serializers.CharField()
    config = serializers.DictField()
    badges = CatalogBadgeSerializer(many=True)


class GamificationCatalogSerializer(serializers.Serializer):
    levels = GamificationLevelSerializer(many=True)
    point_rules = PointRuleSerializer(many=True)
    badges = CatalogBadgeSerializer(many=True)
    rewards = CatalogRewardSerializer(many=True)


def badge_condition(badge):
    requirement = badge.requirement or {}
    kind = requirement.get('type')
    if not kind:
        if 'action' in requirement:
            kind = 'action_count'
        elif 'activity_days' in requirement:
            kind = 'active_days'
    target_value = requirement.get('target', requirement.get(
        'count' if kind == 'action_count' else 'activity_days', 1,
    ))
    try:
        target = max(int(target_value), 1)
    except (ValueError, TypeError):
        return 'Условието се уточнява от екипа.'
    if kind == 'action_count':
        action = ACTION_DESCRIPTIONS.get(requirement.get('action'))
        if action:
            return f'{action}: {target} действия с начислени точки.'
    elif kind == 'active_days':
        return f'Активност с начислени точки в {target} различни дни.'
    elif kind == 'total_points':
        return f'Достигни общо {target} точки.'
    return 'Присъжда се от екипа според описанието на значката.'


def badge_payload(badge):
    return {
        'slug': badge.slug,
        'title': badge.title,
        'description': badge.description,
        'icon': badge.icon,
        'rarity': badge.rarity,
        'points_reward': badge.points_reward,
        'condition': badge_condition(badge),
        'ends_at': badge.ends_at,
    }


def public_point_rules():
    site = SiteSettings.objects.filter(pk=1).first() or SiteSettings()
    features = {
        'poll_vote': site.enable_weekly_poll,
        'song_approved': site.enable_bell_suggestions,
        'meme_approved': site.enable_meme_of_the_week,
    }
    notes = {
        'poll_vote': 'При отговор в анкета.',
        'comment': 'За нов коментар. При изтриване точките се отнемат.',
        'song_approved': 'След одобрение от екипа.',
        'meme_approved': 'След одобрение от екипа.',
        'post_read': 'След поне 15 секунди четене; еднократно за публикация.',
        'calendar_visit': 'При посещение на училищния календар; еднократно на ден.',
    }
    rules = []
    for action, title in ACTION_DESCRIPTIONS.items():
        points, limit = point_value(action), daily_limit(action)
        enabled = features.get(action, True)
        active = enabled and points > 0 and limit != 0
        rules.append({
            'action': action,
            'title': title,
            'points': points,
            'daily_limit': limit,
            'active': active,
            'note': (
                'Разделът временно е изключен.' if not enabled
                else 'Начисляването на точки е изключено.' if not active
                else notes[action]
            ),
        })
    return rules


class GamificationCatalogView(APIView):
    permission_classes = (SiteIsAvailable, AllowAny)

    @extend_schema(
        operation_id='gamification_catalog',
        summary='Актуални правила за точки и публични награди',
        tags=['Геймификация'],
        responses={200: GamificationCatalogSerializer},
    )
    def get(self, request):
        now = timezone.now()
        visible_badges = Badge.objects.filter(active=True, hidden=False).filter(
            Q(starts_at__isnull=True) | Q(starts_at__lte=now),
            Q(ends_at__isnull=True) | Q(ends_at__gte=now),
        )
        badges = list(visible_badges)
        # Only publish rewards with a currently available, non-secret unlock path.
        rewards = DigitalReward.objects.filter(
            active=True, badges__in=badges,
        ).distinct().order_by('kind', 'title').prefetch_related(
            Prefetch('badges', queryset=visible_badges, to_attr='public_badges'),
        )
        payload = {
            'levels': GamificationLevel.objects.order_by('minimum_points', 'number'),
            'point_rules': public_point_rules(),
            'badges': [badge_payload(badge) for badge in badges],
            'rewards': [{
                'key': reward.key,
                'title': reward.title,
                'kind': reward.kind,
                'config': {
                    key: value for key, value in (reward.config or {}).items()
                    if key in ('color', 'secondary_color', 'style', 'text', 'icon')
                },
                'badges': [badge_payload(badge) for badge in reward.public_badges],
            } for reward in rewards],
        }
        return Response(GamificationCatalogSerializer(payload).data)
