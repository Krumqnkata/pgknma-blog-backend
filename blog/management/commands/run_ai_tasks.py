from django.conf import settings
from django.core.management.base import BaseCommand

from blog.ai import config
from blog.ai.client import AIQuotaExceeded
from blog.ai.knowledge_index import sync_knowledge
from blog.ai.moderation import CHECKED_MODELS, check_pending_content, pending_objects
from blog.ai.summaries import post_needs_summary, public_posts_needing_summary, summarize_pending_posts

TASKS = ('summaries', 'moderation', 'knowledge')


class Command(BaseCommand):
    help = (
        'Прави ИИ резюметата на статиите, ИИ проверката на ново съдържание и '
        'обновява знанията на ИИ асистента. Пуска се от systemd таймер на всеки 5 минути.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--only',
            choices=TASKS,
            help='Изпълни само една задача.',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=settings.AI_BATCH_SIZE,
            help='Максимален брой обработени записи от всеки вид (резюмета и проверки).',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Само показва колко записа чакат, без да вика ИИ.',
        )

    def handle(self, *args, **options):
        site_settings = config.get_site_settings()
        limit = max(1, options['limit'])
        tasks = [options['only']] if options['only'] else list(TASKS)

        for task in tasks:
            if task == 'knowledge':
                if not config.assistant_visible(site_settings):
                    self.stdout.write('knowledge: асистентът е изключен в админа.')
                    continue
                if options['dry_run']:
                    self.stdout.write('knowledge: знанията се обновяват при всяко пускане.')
                    continue
                try:
                    stats = sync_knowledge(
                        embed=config.assistant_ai_enabled(site_settings),
                        site_settings=site_settings,
                    )
                except AIQuotaExceeded:
                    self._quota_warning()
                    return
                self.stdout.write(
                    'knowledge: нови {created}, променени {updated}, изтрити {deleted}, embeddings {embedded}.'.format(**stats)
                )
                continue

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
                self._quota_warning()
                return

    def _quota_warning(self):
        self.stdout.write(self.style.WARNING(
            'Дневният безплатен лимит на Workers AI е изчерпан. '
            'Продължава при следващото пускане след 00:00 UTC.'
        ))

    def _pending_count(self, task):
        if task == 'summaries':
            return sum(1 for post in public_posts_needing_summary().iterator() if post_needs_summary(post))
        return sum(pending_objects(model, date_field).count() for model, date_field, _ in CHECKED_MODELS)
