from io import StringIO

from django.contrib.auth.models import Group, Permission, User
from django.core.management import call_command, CommandError
from django.test import RequestFactory, TestCase
from django.urls import reverse

from .admin_navigation import build_admin_navigation
from .admin_roles import ADMIN_ROLE_BY_NAME, ADMIN_ROLES, sync_admin_roles


def _visible_titles(navigation):
    return {
        item["title"]
        for section in navigation
        for item in section["items"]
    }


def _section_titles(navigation):
    return {section.get("title") for section in navigation if section.get("title")}


class AdminRoleSynchronizationTests(TestCase):
    def test_all_predefined_roles_exist_with_their_baseline_permissions(self):
        sync_admin_roles(reset=True)

        for role in ADMIN_ROLES:
            with self.subTest(role=role.name):
                group = Group.objects.get(name=role.name)
                actual = {
                    f"{permission.content_type.app_label}.{permission.codename}"
                    for permission in group.permissions.select_related("content_type")
                }
                self.assertEqual(actual, set(role.permissions))

    def test_normal_sync_preserves_extra_permissions(self):
        sync_admin_roles(reset=True)
        group = Group.objects.get(name="Автори")
        extra = Permission.objects.get(
            content_type__app_label="blog",
            codename="view_event",
        )
        group.permissions.add(extra)

        sync_admin_roles()

        self.assertTrue(group.permissions.filter(pk=extra.pk).exists())

    def test_management_command_is_idempotent(self):
        output = StringIO()
        call_command("sync_admin_roles", stdout=output)
        call_command("sync_admin_roles", stdout=output)

        self.assertEqual(
            Group.objects.filter(name__in=ADMIN_ROLE_BY_NAME).count(),
            len(ADMIN_ROLES),
        )
        self.assertIn("Roles synchronized", output.getvalue())


class RoleTestUserCommandTests(TestCase):
    def test_command_creates_one_safe_staff_account_per_role(self):
        output = StringIO()

        call_command(
            "create_role_test_users",
            password="Safe-Test-Password-123!",
            stdout=output,
        )

        users = User.objects.filter(first_name="[ROLE TEST]").prefetch_related("groups")
        self.assertEqual(users.count(), len(ADMIN_ROLES))
        for user in users:
            with self.subTest(username=user.username):
                self.assertTrue(user.is_staff)
                self.assertTrue(user.is_active)
                self.assertFalse(user.is_superuser)
                self.assertEqual(user.groups.count(), 1)
                self.assertTrue(user.check_password("Safe-Test-Password-123!"))
        self.assertIn("Cleanup:", output.getvalue())

    def test_delete_removes_only_marked_test_accounts(self):
        call_command(
            "create_role_test_users",
            role="author",
            password="Safe-Test-Password-123!",
            stdout=StringIO(),
        )
        real_user = User.objects.create_user(
            username="real_user",
            password="Safe-Test-Password-123!",
        )

        call_command(
            "create_role_test_users",
            delete=True,
            stdout=StringIO(),
        )

        self.assertFalse(User.objects.filter(username="role_test_author").exists())
        self.assertTrue(User.objects.filter(pk=real_user.pk).exists())

    def test_command_refuses_to_overwrite_a_real_account(self):
        User.objects.create_user(
            username="role_test_author",
            password="Safe-Test-Password-123!",
        )

        with self.assertRaisesMessage(CommandError, "Refusing to overwrite"):
            call_command(
                "create_role_test_users",
                role="author",
                password="Replacement-Password-123!",
                stdout=StringIO(),
            )


class AdminNavigationPermissionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        sync_admin_roles(reset=True)

    def _navigation_for_role(self, role_name):
        user = User.objects.create_user(
            username=f"nav-{User.objects.count()}",
            password="test-password",
            is_staff=True,
        )
        user.groups.add(Group.objects.get(name=role_name))
        request = RequestFactory().get("/admin/")
        request.user = user
        return build_admin_navigation(request)

    def test_author_sees_own_content_tools_only(self):
        navigation = self._navigation_for_role("Автори")
        titles = _visible_titles(navigation)

        self.assertIn("Публикации", titles)
        self.assertIn("Снимки към публикации", titles)
        self.assertNotIn("Потребители", titles)
        self.assertNotIn("Значки", titles)
        self.assertNotIn("Текущи сигнали", titles)
        self.assertNotIn("Администрация", _section_titles(navigation))

    def test_gamification_manager_sees_only_relevant_section_and_read_only_users(self):
        navigation = self._navigation_for_role("Мениджъри на геймификацията")
        titles = _visible_titles(navigation)

        self.assertIn("Значки", titles)
        self.assertIn("Мисии", titles)
        self.assertIn("Потребители", titles)
        self.assertNotIn("Публикации", titles)
        self.assertNotIn("Групи и роли", titles)
        self.assertNotIn("Текущи сигнали", titles)

    def test_empty_sections_are_removed_for_moderator(self):
        navigation = self._navigation_for_role("Модератори")
        sections = _section_titles(navigation)
        titles = _visible_titles(navigation)

        self.assertIn("Ангажираност на потребителите", sections)
        self.assertIn("Коментари", titles)
        self.assertNotIn("Геймификация", sections)
        self.assertNotIn("Валентинки", sections)

    def test_superuser_sees_every_section(self):
        user = User.objects.create_superuser(
            username="nav-root",
            email="root@example.com",
            password="test-password",
        )
        request = RequestFactory().get("/admin/")
        request.user = user

        sections = _section_titles(build_admin_navigation(request))

        self.assertTrue(
            {
                "Управление на съдържанието",
                "Ангажираност на потребителите",
                "Геймификация",
                "Валентинки",
                "Администрация",
                "Аутентикация и оторизация",
            }.issubset(sections)
        )


class AdminDashboardPermissionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        sync_admin_roles(reset=True)

    def test_author_dashboard_hides_unrelated_quick_actions(self):
        user = User.objects.create_user(
            username="dashboard-author",
            password="test-password",
            is_staff=True,
        )
        user.groups.add(Group.objects.get(name="Автори"))
        self.client.force_login(user)

        response = self.client.get(reverse("admin:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Създайте нова статия.")
        self.assertNotContains(response, "Прегледайте предложенията.")
        self.assertNotContains(response, "Създайте нова анкета.")
        self.assertNotContains(response, 'id="site-statistics"')

    def test_site_manager_sees_statistics_but_not_role_management(self):
        user = User.objects.create_user(
            username="dashboard-site-manager",
            password="test-password",
            is_staff=True,
        )
        user.groups.add(Group.objects.get(name="Управление на сайта"))
        self.client.force_login(user)

        response = self.client.get(reverse("admin:index"))
        request = RequestFactory().get("/admin/")
        request.user = user
        titles = _visible_titles(build_admin_navigation(request))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="site-statistics"')
        self.assertNotIn("Групи и роли", titles)
