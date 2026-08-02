"""Assign a random built-in avatar preset to blog users."""

import random

from django.core.management.base import BaseCommand
from django.utils import timezone

from blog.avatars import AVATAR_PRESET_IDS
from blog.models import UserProfile


class Command(BaseCommand):
    help = (
        'Задава произволен готов аватар на профилите без избрана профилна снимка. '
        'Използвайте --force, за да замените и вече зададените аватари.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='Заменя и качени, Google и вече избрани готови аватари.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Само показва колко профила ще бъдат променени.',
        )

    def handle(self, *args, **options):
        force = options['force']
        dry_run = options['dry_run']
        presets = tuple(sorted(AVATAR_PRESET_IDS))

        queryset = UserProfile.objects.select_related('user').order_by('pk')
        if not force:
            queryset = queryset.filter(
                avatar_source=UserProfile.AvatarSource.INITIALS,
                avatar__isnull=True,
                avatar_preset='',
            )

        profiles = list(queryset)
        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f'Ще бъдат променени {len(profiles)} профила '
                    f'({"всички" if force else "само без снимка"}).'
                )
            )
            return

        chooser = random.SystemRandom()
        changed = 0
        for profile in profiles:
            if profile.avatar:
                # --force is explicit: remove the old uploaded file too.
                profile.avatar.delete(save=False)
            profile.avatar = None
            profile.avatar_source = UserProfile.AvatarSource.PRESET
            profile.avatar_preset = chooser.choice(presets)
            profile.avatar_user_selected = False
            profile.avatar_updated_at = timezone.now()
            profile.save(
                update_fields=(
                    'avatar',
                    'avatar_source',
                    'avatar_preset',
                    'avatar_user_selected',
                    'avatar_updated_at',
                )
            )
            changed += 1

        self.stdout.write(
            self.style.SUCCESS(
                f'Зададени са случайни готови аватари на {changed} профила.'
            )
        )
