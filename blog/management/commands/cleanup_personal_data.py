import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from blog.data_retention import cleanup_personal_data


class Command(BaseCommand):
    help = 'Deletes security and network metadata outside the retention window.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=getattr(settings, 'PERSONAL_DATA_RETENTION_DAYS', 90),
            help='Retention period in days (default: configured value or 90).',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Only print how many records would be affected.',
        )

    def handle(self, *args, **options):
        days = options['days']
        if days < 1:
            raise CommandError('--days must be a positive integer.')

        counts = cleanup_personal_data(
            days=days,
            dry_run=options['dry_run'],
        )
        result = {
            'retention_days': days,
            'dry_run': options['dry_run'],
            **counts,
        }
        self.stdout.write(json.dumps(result, sort_keys=True))
