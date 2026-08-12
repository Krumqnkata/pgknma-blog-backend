from django.core.management.base import BaseCommand
from django.db import transaction

from blog.notification_sources import (
    backfill_legacy_notification_sources,
    synchronize_all_notification_sources,
)


class Command(BaseCommand):
    help = (
        'Свързва старите автоматични известия с тяхното съдържание и '
        'премахва невалидните действия.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Показва резултата, без да записва промени.',
        )

    def handle(self, *args, **options):
        with transaction.atomic():
            backfill = backfill_legacy_notification_sources()
            synchronized = synchronize_all_notification_sources()
            if options['dry_run']:
                transaction.set_rollback(True)

        # Keep command output ASCII-safe for Windows deployment/test consoles.
        prefix = 'Dry run' if options['dry_run'] else 'Repair complete'
        values = {
            **{f'backfill_{key}': value for key, value in backfill.items()},
            **{f'sync_{key}': value for key, value in synchronized.items()},
        }
        self.stdout.write(self.style.SUCCESS(
            f'{prefix}: ' + ', '.join(f'{key}={value}' for key, value in values.items())
        ))
