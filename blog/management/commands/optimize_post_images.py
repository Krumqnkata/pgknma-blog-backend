import math
import time
from collections import Counter

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from blog.models import PostImage
from blog.post_image_backfill import create_missing_variants


class Command(BaseCommand):
    help = (
        'Create missing WebP gallery variants in bounded batches. '
        'Read-only by default; --apply writes variants and their DB references. '
        'Originals and their URLs are preserved.'
    )
    requires_migrations_checks = True

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument('--apply', action='store_true', help='Process this batch.')
        mode.add_argument('--dry-run', action='store_true', help='List candidates only (default).')
        parser.add_argument('--post-id', type=int, help='Restrict to one post.')
        parser.add_argument('--limit', type=int, default=10, help='Maximum images examined per run (default: 10).')
        parser.add_argument('--after-id', type=int, default=0, help='Continue after this PostImage ID.')
        parser.add_argument('--pause', type=float, default=0.5, help='Seconds between images (default: 0.5).')

    def handle(self, *args, **options):
        if options['limit'] < 1:
            raise CommandError('--limit must be at least 1.')
        if options['after_id'] < 0:
            raise CommandError('--after-id must be non-negative.')
        if options['post_id'] is not None and options['post_id'] < 1:
            raise CommandError('--post-id must be at least 1.')
        if not math.isfinite(options['pause']) or not 0 <= options['pause'] <= 60:
            raise CommandError('--pause must be between 0 and 60 seconds.')
        if options['apply'] and options['dry_run']:
            raise CommandError('Choose --apply or --dry-run, not both.')

        candidates = PostImage.objects.filter(
            Q(display_image='') | Q(display_image__isnull=True)
            | Q(thumbnail_image='') | Q(thumbnail_image__isnull=True),
            pk__gt=options['after_id'],
        ).exclude(image='').exclude(image__isnull=True)
        if options['post_id'] is not None:
            candidates = candidates.filter(post_id=options['post_id'])
        total = candidates.count()
        batch = list(candidates.select_related('post').order_by('pk')[:options['limit']])
        self.stdout.write(f'Pending after cursor: {total}; this batch: {len(batch)}')
        if not batch:
            return

        if not options['apply']:
            self.stdout.write('DRY RUN: no files read or written; no DB changes.')
            for image in batch:
                self.stdout.write(f'Would process image={image.pk} post={image.post_id}')
            self.stdout.write('Use --apply with the same filters to process this batch.')
            return

        counts = Counter()
        original_bytes = added_bytes = 0
        for index, image in enumerate(batch, 1):
            if index > 1 and options['pause']:
                time.sleep(options['pause'])
            started = time.monotonic()
            try:
                result = create_missing_variants(image)
                counts[result.status] += 1
                original_bytes += result.original_bytes
                added_bytes += result.added_bytes
                self.stdout.write(
                    f'[{index}/{len(batch)}] image={image.pk}: {result.status}; '
                    f'{time.monotonic() - started:.2f}s; '
                    f'added={result.added_bytes} bytes'
                )
            except Exception as exc:
                counts['failed'] += 1
                self.stderr.write(f'[{index}/{len(batch)}] image={image.pk}: FAILED: {exc}')
            self.stdout.flush()
            self.stderr.flush()

        self.stdout.write(
            f'Result: optimized={counts["optimized"]}, failed={counts["failed"]}, '
            f'animated_skipped={counts["animated-skipped"]}, '
            f'changed_skipped={counts["changed-skipped"]}, '
            f'already_ready={counts["already-ready"]}; '
            f'originals_read={original_bytes} bytes; added={added_bytes} bytes'
        )
        self.stdout.write(
            f'To continue past this batch, keep the filters and use --after-id {batch[-1].pk}. '
            'To retry failed IDs, rerun without that cursor after resolving their errors. '
            'Completed images are skipped automatically.'
        )
        if counts['failed']:
            raise CommandError(f'{counts["failed"]} image(s) failed; see IDs above.')
