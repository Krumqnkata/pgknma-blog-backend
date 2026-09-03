#!/usr/bin/env python
"""Award the founding-member badge to active users already in the blog.

Examples:
    python scripts/award_blog_pioneer_badge.py --dry-run
    python scripts/award_blog_pioneer_badge.py
    python scripts/award_blog_pioneer_badge.py --select-title
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding='utf-8')
    except (AttributeError, OSError):
        pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cms.settings')

import django  # noqa: E402

django.setup()

from django.contrib.auth import get_user_model  # noqa: E402
from django.db import transaction  # noqa: E402

from blog.gamification.services import award_badge, select_digital_reward  # noqa: E402
from blog.models import Badge, DigitalReward  # noqa: E402


BADGE_SLUG = 'blog-pioneer'
REWARD_KEY = 'blog-pioneer-title'
DEFAULT_CUTOFF = datetime(2026, 8, 9, 23, 59, 59, tzinfo=ZoneInfo('Europe/Sofia'))


def eligible_users(*, cutoff=DEFAULT_CUTOFF):
    return get_user_model().objects.filter(
        is_active=True,
        date_joined__lte=cutoff,
    ).order_by('pk')


def _ensure_badge_and_reward():
    reward, _ = DigitalReward.objects.update_or_create(
        key=REWARD_KEY,
        defaults={
            'title': 'Титла „Първопроходец“',
            'kind': DigitalReward.Kind.USER_TITLE,
            'config': {
                'color': '#7c3aed',
                'secondary_color': '#f59e0b',
                'style': 'glow',
                'text': 'Първопроходец',
            },
            'active': True,
        },
    )
    badge, _ = Badge.objects.update_or_create(
        slug=BADGE_SLUG,
        defaults={
            'title': 'Първопроходец на блога',
            'description': 'Специално отличие за хората, които бяха част от блога в самото начало.',
            'icon': 'sparkles',
            'rarity': Badge.Rarity.SEASONAL,
            'requirement': {'type': 'manual'},
            'active': True,
            'hidden': False,
            'points_reward': 0,
            'repeatable': False,
        },
    )
    badge.digital_rewards.add(reward)
    return badge, reward


def award_to_users(users, *, select_title=False):
    awarded = 0
    already_owned = 0
    selected = 0
    with transaction.atomic():
        badge, reward = _ensure_badge_and_reward()
        for user in users:
            _, created = award_badge(
                user,
                badge,
                reason='Първоначален член на блога',
                award_key='founding-member-2026',
            )
            awarded += int(created)
            already_owned += int(not created)
            if select_title:
                select_digital_reward(user, reward.key, selected=True)
                selected += 1

    result = {
        'awarded': awarded,
        'already_owned': already_owned,
        'selected': selected,
    }
    print(
        f"Готово: нови значки {awarded}, вече притежавани {already_owned}, "
        f"активирани титли {selected}."
    )
    return result


def parse_args():
    parser = argparse.ArgumentParser(description='Дава значката „Първопроходец на блога“.')
    parser.add_argument('--dry-run', action='store_true', help='Показва кои профили отговарят, без запис.')
    parser.add_argument('--select-title', action='store_true', help='Активира титлата в профила.')
    return parser.parse_args()


def main():
    args = parse_args()
    users = list(eligible_users())
    print(f'Намерени активни потребители до {DEFAULT_CUTOFF:%d.%m.%Y %H:%M:%S}: {len(users)}.')
    if args.dry_run:
        for user in users:
            print(f'- {user.username}')
        return 0
    award_to_users(users, select_title=args.select_title)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
