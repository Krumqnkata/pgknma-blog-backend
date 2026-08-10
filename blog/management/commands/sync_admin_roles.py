from django.core.management.base import BaseCommand

from blog.admin_roles import ADMIN_ROLES, sync_admin_roles


class Command(BaseCommand):
    help = "Създава и синхронизира стандартните роли за Django admin панела."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Премахва допълнително добавените права и връща точния стандартен набор.",
        )

    def handle(self, *args, **options):
        result = sync_admin_roles(reset=options["reset"])
        self.stdout.write(
            self.style.SUCCESS(f"Roles synchronized: {len(ADMIN_ROLES)}")
        )
        self.stdout.write(
            f"Created: {result['created']}; updated: {result['updated']}."
        )
        if result["missing"]:
            self.stdout.write(
                self.style.WARNING(
                    "Missing permissions: " + ", ".join(sorted(result["missing"]))
                )
            )
