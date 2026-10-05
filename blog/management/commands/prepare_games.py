from datetime import date
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from blog.games.content import build_pack, validate_database
from blog.games.definitions import GAMES, action_name
from blog.games.models import GamePuzzle, GameSettings
from blog.models import GamificationPointRule


class Command(BaseCommand):
    help = 'Prepare and verify 400 days of games offline; preserves existing tasks and settings.'

    def add_arguments(self, parser):
        parser.add_argument('--check', action='store_true', help='Read-only validation of the installed pack.')
        parser.add_argument('--start-date', type=date.fromisoformat)
        parser.add_argument('--activate', action='store_true')

    @transaction.atomic
    def handle(self, *args, **options):
        if options['check']:
            if options['activate'] or options['start_date']:
                raise CommandError('--check cannot change settings')
        else:
            pack = build_pack()
            existing = {row.key: row for row in GamePuzzle.objects.filter(key__in=[row['key'] for row in pack])}
            for row in pack:
                old = existing.get(row['key'])
                if old and (old.payload != row['payload'] or old.fingerprint != row['fingerprint']):
                    raise CommandError(f"Existing task differs: {row['key']}. Use a new content version; never overwrite played tasks.")
            GamePuzzle.objects.bulk_create([GamePuzzle(**row) for row in pack if row['key'] not in existing], batch_size=200)
            for game in GAMES:
                GamificationPointRule.objects.get_or_create(action=action_name(game), defaults={'points':5, 'daily_limit':None})
            config, _ = GameSettings.objects.get_or_create(pk=1)
            if options['start_date']:
                config.start_date = options['start_date']
            if options['activate']:
                config.enabled = True
            config.full_clean()
            config.save()
        errors = validate_database()
        if errors:
            raise CommandError('\n'.join(errors[:20]))
        self.stdout.write(self.style.SUCCESS('Verified: 400 daily tasks per game; 4000 prepared tasks; 2500 quiz questions; 600 words; 100 reading texts.'))
