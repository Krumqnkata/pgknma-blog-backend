#!/usr/bin/env python
"""Create the official digital-reward catalogue and optionally grant it.

Examples:
    python seed_official_digital_rewards.py
    python seed_official_digital_rewards.py --link-badges
    python seed_official_digital_rewards.py --link-badges --grant-to admin
    python seed_official_digital_rewards.py --grant-to admin --select
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding='utf-8')
    except (AttributeError, OSError):
        pass

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cms.settings')

import django  # noqa: E402

django.setup()

from django.contrib.auth import get_user_model  # noqa: E402
from django.db import transaction  # noqa: E402

from blog.gamification.services import (  # noqa: E402
    select_digital_reward,
    unlock_digital_reward,
)
from blog.models import Badge, DigitalReward  # noqa: E402


OFFICIAL_REWARDS = (
    {
        'key': 'emerald-reader-frame',
        'title': 'Изумрудена рамка на читателя',
        'kind': DigitalReward.Kind.AVATAR_FRAME,
        'badge_slug': 'regular-reader',
        'config': {'color': '#10b981', 'secondary_color': '#22d3ee', 'style': 'gradient'},
    },
    {
        'key': 'golden-achievement-frame',
        'title': 'Златна рамка на постиженията',
        'kind': DigitalReward.Kind.AVATAR_FRAME,
        'badge_slug': 'event-participant',
        'config': {'color': '#f59e0b', 'secondary_color': '#facc15', 'style': 'glow'},
    },
    {
        'key': 'school-spirit-border',
        'title': 'Рамка „Училищен дух“',
        'kind': DigitalReward.Kind.PROFILE_BORDER,
        'badge_slug': 'student-voice',
        'config': {'color': '#2563eb', 'secondary_color': '#7c3aed', 'style': 'gradient'},
    },
    {
        'key': 'night-talent-border',
        'title': 'Рамка „Нощен талант“',
        'kind': DigitalReward.Kind.PROFILE_BORDER,
        'badge_slug': 'music-selector',
        'config': {'color': '#312e81', 'secondary_color': '#c026d3', 'style': 'glow'},
    },
    {
        'key': 'northern-lights-theme',
        'title': 'Тема „Северно сияние“',
        'kind': DigitalReward.Kind.PROFILE_THEME,
        'badge_slug': 'meme-master',
        'config': {'color': '#0f766e', 'secondary_color': '#8b5cf6', 'style': 'gradient'},
    },
    {
        'key': 'sunrise-theme',
        'title': 'Тема „Изгрев“',
        'kind': DigitalReward.Kind.PROFILE_THEME,
        'badge_slug': 'first-comment',
        'config': {'color': '#fb7185', 'secondary_color': '#facc15', 'style': 'gradient'},
    },
    {
        'key': 'active-student-title',
        'title': 'Титла „Активен ученик“',
        'kind': DigitalReward.Kind.USER_TITLE,
        'badge_slug': 'first-comment',
        'config': {'color': '#16a34a', 'style': 'solid', 'text': 'Активен ученик'},
    },
    {
        'key': 'digital-ambassador-title',
        'title': 'Титла „Дигитален посланик“',
        'kind': DigitalReward.Kind.USER_TITLE,
        'badge_slug': 'regular-reader',
        'config': {'color': '#7c3aed', 'style': 'solid', 'text': 'Дигитален посланик'},
    },
    {
        'key': 'community-star-icon',
        'title': 'Икона „Звезда на общността“',
        'kind': DigitalReward.Kind.SPECIAL_ICON,
        'badge_slug': 'student-voice',
        'config': {'color': '#facc15', 'secondary_color': '#f97316', 'style': 'glow', 'icon': 'star'},
    },
    {
        'key': 'school-champion-icon',
        'title': 'Икона „Училищен шампион“',
        'kind': DigitalReward.Kind.SPECIAL_ICON,
        'badge_slug': 'event-participant',
        'config': {'color': '#eab308', 'secondary_color': '#ef4444', 'style': 'glow', 'icon': 'trophy'},
    },
)


def _resolve_user(value):
    if value is None or hasattr(value, '_meta'):
        return value
    user_model = get_user_model()
    try:
        return user_model.objects.get(username=value)
    except user_model.DoesNotExist as exc:
        raise SystemExit(f'Няма потребител с име „{value}“.') from exc


def seed_rewards(*, link_badges=False, grant_to=None, select=False):
    user = _resolve_user(grant_to)
    created_count = 0
    linked_count = 0
    granted_count = 0

    with transaction.atomic():
        for definition in OFFICIAL_REWARDS:
            reward, created = DigitalReward.objects.update_or_create(
                key=definition['key'],
                defaults={
                    'title': definition['title'],
                    'kind': definition['kind'],
                    'config': definition['config'],
                    'active': True,
                },
            )
            created_count += int(created)

            if link_badges:
                badge = Badge.objects.get(slug=definition['badge_slug'])
                before = badge.digital_rewards.filter(pk=reward.pk).exists()
                badge.digital_rewards.add(reward)
                linked_count += int(not before)

            if user is not None:
                _, unlocked = unlock_digital_reward(user, reward)
                granted_count += int(unlocked)
                if select:
                    select_digital_reward(user, reward.key, selected=True)

            print(f"✓ {reward.title}: {'създадена' if created else 'обновена'}")

    print(
        f'Готово: {len(OFFICIAL_REWARDS)} награди; '
        f'нови {created_count}, свързани {linked_count}, дадени {granted_count}.'
    )
    return {
        'created': created_count,
        'linked': linked_count,
        'granted': granted_count,
    }


def parse_args():
    parser = argparse.ArgumentParser(description='Добавя официалните дигитални награди.')
    parser.add_argument('--link-badges', action='store_true', help='Свързва наградите със значките им.')
    parser.add_argument('--grant-to', metavar='USERNAME', help='Дава каталога на избран потребител.')
    parser.add_argument('--select', action='store_true', help='Активира дадените награди визуално.')
    args = parser.parse_args()
    if args.select and not args.grant_to:
        parser.error('--select изисква --grant-to.')
    return args


def main():
    args = parse_args()
    seed_rewards(
        link_badges=args.link_badges,
        grant_to=args.grant_to,
        select=args.select,
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
