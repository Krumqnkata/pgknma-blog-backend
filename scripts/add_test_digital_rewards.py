#!/usr/bin/env python
"""Add or remove a reusable set of test digital rewards for one user.

Examples:
    python scripts/add_test_digital_rewards.py --username admin
    python scripts/add_test_digital_rewards.py --username admin --no-select
    python scripts/add_test_digital_rewards.py --username admin --remove
    python scripts/add_test_digital_rewards.py --purge
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

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cms.settings')

import django  # noqa: E402

django.setup()

from django.contrib.auth import get_user_model  # noqa: E402
from django.db import transaction  # noqa: E402
from django.db.utils import OperationalError, ProgrammingError  # noqa: E402

from blog.gamification.services import (  # noqa: E402
    select_digital_reward,
    unlock_digital_reward,
)
from blog.models import DigitalReward, UserDigitalReward  # noqa: E402


REWARD_DEFINITIONS = (
    {
        'key': 'test-violet-avatar-frame',
        'title': 'Виолетова рамка',
        'kind': DigitalReward.Kind.AVATAR_FRAME,
        'config': {
            'color': '#7c3aed',
            'secondary_color': '#22d3ee',
            'style': 'gradient',
        },
    },
    {
        'key': 'test-golden-profile-border',
        'title': 'Златна профилна рамка',
        'kind': DigitalReward.Kind.PROFILE_BORDER,
        'config': {
            'color': '#f59e0b',
            'secondary_color': '#ef4444',
            'style': 'glow',
        },
    },
    {
        'key': 'test-aurora-profile-theme',
        'title': 'Тема Аврора',
        'kind': DigitalReward.Kind.PROFILE_THEME,
        'config': {
            'color': '#0ea5e9',
            'secondary_color': '#8b5cf6',
            'style': 'gradient',
        },
    },
    {
        'key': 'test-school-ambassador-title',
        'title': 'Титла „Училищен посланик“',
        'kind': DigitalReward.Kind.USER_TITLE,
        'config': {
            'color': '#8b5cf6',
            'secondary_color': '#22d3ee',
            'style': 'solid',
            'text': 'Училищен посланик',
        },
    },
    {
        'key': 'test-special-star',
        'title': 'Специална звезда',
        'kind': DigitalReward.Kind.SPECIAL_ICON,
        'config': {
            'color': '#facc15',
            'secondary_color': '#f97316',
            'style': 'glow',
            'icon': 'star',
        },
    },
)

TEST_REWARD_KEYS = tuple(item['key'] for item in REWARD_DEFINITIONS)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Добавя тестови дигитални награди на избран потребител.',
    )
    parser.add_argument(
        '--username',
        help='Потребителското име, което ще получи наградите.',
    )
    parser.add_argument(
        '--no-select',
        action='store_true',
        help='Добавя наградите, без да ги активира визуално.',
    )
    parser.add_argument(
        '--remove',
        action='store_true',
        help='Премахва тестовите награди само от избрания потребител.',
    )
    parser.add_argument(
        '--purge',
        action='store_true',
        help='Изтрива тестовите дефиниции и назначенията им за всички потребители.',
    )
    args = parser.parse_args()
    if not args.purge and not args.username:
        parser.error('--username е задължително, освен при --purge.')
    if args.purge and (args.remove or args.no_select):
        parser.error('--purge не може да се комбинира с --remove или --no-select.')
    return args


def get_user(username: str):
    user_model = get_user_model()
    try:
        return user_model.objects.get(username=username)
    except user_model.DoesNotExist:
        examples = ', '.join(
            user_model.objects.order_by('username').values_list('username', flat=True)[:10]
        )
        suffix = f' Налични потребители: {examples}.' if examples else ''
        raise SystemExit(f'Няма потребител с име „{username}“.{suffix}')


def add_rewards(user, *, select: bool) -> None:
    with transaction.atomic():
        for definition in REWARD_DEFINITIONS:
            reward, created = DigitalReward.objects.update_or_create(
                key=definition['key'],
                defaults={
                    'title': definition['title'],
                    'kind': definition['kind'],
                    'config': definition['config'],
                    'active': True,
                },
            )
            _, unlocked = unlock_digital_reward(user, reward)
            if select:
                select_digital_reward(user, reward.key, selected=True)
            definition_state = 'създадена' if created else 'обновена'
            ownership_state = 'дадена' if unlocked else 'вече притежавана'
            selected_state = ', активирана' if select else ''
            print(
                f'✓ {reward.title}: {definition_state}, '
                f'{ownership_state}{selected_state}'
            )

    print(
        f'\nГотово: {len(REWARD_DEFINITIONS)} тестови награди '
        f'са налични за {user.username}.'
    )


def remove_rewards(user) -> None:
    deleted, _ = UserDigitalReward.objects.filter(
        user=user,
        reward__key__in=TEST_REWARD_KEYS,
    ).delete()
    print(f'Премахнати назначения за {user.username}: {deleted}.')


def purge_rewards() -> None:
    deleted, details = DigitalReward.objects.filter(key__in=TEST_REWARD_KEYS).delete()
    print(f'Изтрити тестови записи: {deleted}.')
    if details:
        print(f'Подробности: {details}')


def main() -> int:
    args = parse_args()
    try:
        if args.purge:
            purge_rewards()
            return 0
        user = get_user(args.username)
        if args.remove:
            remove_rewards(user)
        else:
            add_rewards(user, select=not args.no_select)
    except (OperationalError, ProgrammingError) as exc:
        print(
            'Базата няма необходимите gamification таблици. '
            'Първо проверете и приложете Django миграциите.',
            file=sys.stderr,
        )
        print(f'Техническа грешка: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
