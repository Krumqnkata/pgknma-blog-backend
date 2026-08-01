from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from blog.models import BlogLoginSession


class Command(BaseCommand):
    help = 'Изтрива изтекли и прекратени истории на входове след срока за пазене.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=getattr(settings, 'BLOG_SESSION_RETENTION_DAYS', 90),
            help='Срок за пазене в дни (по подразбиране 90).',
        )

    def handle(self, *args, **options):
        days = options['days']
        if days < 1:
            raise CommandError('--days трябва да е положително число.')

        cutoff = timezone.now() - timedelta(days=days)
        queryset = BlogLoginSession.objects.filter(created_at__lt=cutoff)
        count = queryset.count()
        queryset.delete()
        self.stdout.write(
            self.style.SUCCESS(
                f'Deleted login session history: {count}'
            )
        )
