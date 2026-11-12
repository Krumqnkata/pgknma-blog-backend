from django.conf import settings
from django.core.management.base import BaseCommand

from blog.ai import config
from blog.ai.client import AIQuotaExceeded
from blog.ai.moderation import CHECKED_MODELS, check_pending_content, pending_objects
from blog.ai.summaries import post_needs_summary, public_posts_needing_summary, summarize_pending_posts


class Command(BaseCommand):
    help = (
        'Прави ИИ резюметата на статиите и ИИ проверката на ново съдържание. '
        'Пуска се от systemd таймер на всеки 5 минути.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--only',
            choices=('summaries', 'moderation'),
            help='Изпълни само едната задача.',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=settings.AI_BATCH_SIZE,
            help='Максимален брой обработени записи от всеки вид.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Само показва колко записа чакат, без да вика ИИ.',
        )

    def handle(self, *args, **options):
        site_settings = config.get_site_settings()
        limit = max(1, options['limit'])
        tasks = [options['only']] if options['only'] else ['summaries', 'moderation']

        for task in tasks:
            feature = config.SUMMARIES if task == 'summaries' else config.MODERATION
            if not config.ai_feature_enabled(feature, site_settings):
                self.stdout.write(f'{task}: изключено (копче в админа или липсващи ключове).')
                continue
            if options['dry_run']:
                self.stdout.write(f'{task}: чакащи записи — {self._pending_count(task)}.')
                continue
            try:
                if task == 'summaries':
                    done, failed = summarize_pending_posts(limit)
                    self.stdout.write(f'summaries: готови {done}, неуспешни {failed}.')
                else:
                    checked, flagged = check_pending_content(limit)
                    self.stdout.write(f'moderation: проверени {checked}, маркирани {flagged}.')
            except AIQuotaExceeded:
                self.stdout.write(self.style.WARNING(
                    'Дневният безплатен лимит на Workers AI е изчерпан. '
                    'Продължава при следващото пускане след 00:00 UTC.'
                ))
                return

    def _pending_count(self, task):
        if task == 'summaries':
            return sum(1 for post in public_posts_needing_summary().iterator() if post_needs_summary(post))
        return sum(pending_objects(model, date_field).count() for model, date_field, _ in CHECKED_MODELS)
