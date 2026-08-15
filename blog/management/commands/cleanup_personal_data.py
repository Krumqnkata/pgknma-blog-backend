import json
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from blog.data_retention import cleanup_personal_data
from blog.models import PersonalDataCleanupRun


class Command(BaseCommand):
    help = (
        'Deletes expired metadata, archived notifications and old team-chat '
        'messages outside the retention window.'
    )

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

        now = timezone.now()
        cleanup_run = PersonalDataCleanupRun.objects.create(
            retention_days=days,
            cutoff_at=now - timedelta(days=days),
            dry_run=options['dry_run'],
        )

        try:
            counts = cleanup_personal_data(
                days=days,
                now=now,
                dry_run=options['dry_run'],
            )
        except Exception as exc:
            cleanup_run.status = PersonalDataCleanupRun.STATUS_FAILED
            cleanup_run.finished_at = timezone.now()
            cleanup_run.error_message = (
                str(exc).strip() or exc.__class__.__name__
            )[:4000]
            cleanup_run.save(
                update_fields=(
                    'status',
                    'finished_at',
                    'error_message',
                )
            )
            raise

        cleanup_run.status = PersonalDataCleanupRun.STATUS_SUCCEEDED
        cleanup_run.finished_at = timezone.now()
        cleanup_run.result_counts = counts
        cleanup_run.save(
            update_fields=(
                'status',
                'finished_at',
                'result_counts',
            )
        )
        result = {
            'cleanup_run_id': cleanup_run.pk,
            'status': cleanup_run.status,
            'retention_days': days,
            'dry_run': options['dry_run'],
            **counts,
        }
        self.stdout.write(json.dumps(result, sort_keys=True))
