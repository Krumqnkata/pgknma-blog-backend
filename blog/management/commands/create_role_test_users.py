import re
import secrets
import string

from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from blog.admin_roles import ADMIN_ROLE_BY_NAME, sync_admin_roles


TEST_USER_MARKER = "[ROLE TEST]"
DEFAULT_PREFIX = "role_test"
ROLE_SLUGS = {
    "author": "Автори",
    "editor": "Редактори",
    "moderator": "Модератори",
    "gamification": "Мениджъри на геймификацията",
    "valentine": "Мениджъри на Валентинки",
    "site-manager": "Управление на сайта",
}


def _generate_password(length=24):
    alphabet = string.ascii_letters + string.digits + "-_!"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _username(prefix, role_slug):
    return f"{prefix}_{role_slug.replace('-', '_')}"


class Command(BaseCommand):
    help = "Creates isolated staff accounts for testing the predefined admin roles."

    def add_arguments(self, parser):
        parser.add_argument(
            "--role",
            choices=("all", *ROLE_SLUGS),
            default="all",
            help="Create one selected role account or all role accounts.",
        )
        parser.add_argument(
            "--prefix",
            default=DEFAULT_PREFIX,
            help="Username prefix (default: role_test).",
        )
        parser.add_argument(
            "--password",
            help="Shared test password. If omitted, a strong random password is generated.",
        )
        parser.add_argument(
            "--show-permissions",
            action="store_true",
            help="Print every effective permission for the created accounts.",
        )
        parser.add_argument(
            "--delete",
            action="store_true",
            help="Delete only test accounts previously created by this command.",
        )

    def handle(self, *args, **options):
        prefix = options["prefix"]
        if not re.fullmatch(r"[a-z0-9_]{3,32}", prefix):
            raise CommandError(
                "Prefix must contain 3-32 lowercase Latin letters, numbers or underscores."
            )

        selected = ROLE_SLUGS.items()
        if options["role"] != "all":
            selected = ((options["role"], ROLE_SLUGS[options["role"]]),)
        selected = tuple(selected)

        if options["delete"]:
            self._delete_accounts(prefix, selected)
            return

        sync_result = sync_admin_roles()
        if sync_result["missing"]:
            raise CommandError(
                "Some role permissions do not exist. Run 'python manage.py migrate' first. "
                "Missing: " + ", ".join(sorted(sync_result["missing"]))
            )

        password = options["password"] or _generate_password()
        accounts = []
        with transaction.atomic():
            for role_slug, role_name in selected:
                username = _username(prefix, role_slug)
                existing = User.objects.filter(username=username).first()
                if existing is not None and existing.first_name != TEST_USER_MARKER:
                    raise CommandError(
                        f"Refusing to overwrite existing non-test user: {username}"
                    )

                user, created = User.objects.get_or_create(
                    username=username,
                    defaults={
                        "first_name": TEST_USER_MARKER,
                        "last_name": role_slug,
                        "is_staff": True,
                        "is_active": True,
                        "is_superuser": False,
                    },
                )
                user.first_name = TEST_USER_MARKER
                user.last_name = role_slug
                user.is_staff = True
                user.is_active = True
                user.is_superuser = False
                user.set_password(password)
                user.save()
                user.groups.set([Group.objects.get(name=role_name)])
                user.user_permissions.clear()
                accounts.append((user, role_name, created))

        self.stdout.write(self.style.SUCCESS(f"Test accounts ready: {len(accounts)}"))
        self.stdout.write(f"Shared password: {password}")
        self.stdout.write("Login URL: /admin/")
        for user, role_name, created in accounts:
            state = "created" if created else "updated"
            permission_count = len(user.get_all_permissions())
            self.stdout.write(
                f"- {user.username}: {state}; permissions={permission_count}"
            )
            if options["show_permissions"]:
                for permission in sorted(user.get_all_permissions()):
                    self.stdout.write(f"    {permission}")

        self.stdout.write(
            "Cleanup: python manage.py create_role_test_users --delete"
        )

    def _delete_accounts(self, prefix, selected):
        usernames = [_username(prefix, role_slug) for role_slug, _ in selected]
        queryset = User.objects.filter(
            username__in=usernames,
            first_name=TEST_USER_MARKER,
            is_superuser=False,
        )
        deleted_count = queryset.count()
        queryset.delete()
        self.stdout.write(self.style.SUCCESS(f"Deleted test accounts: {deleted_count}"))
