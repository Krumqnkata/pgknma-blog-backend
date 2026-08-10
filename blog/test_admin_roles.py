from io import StringIO

from django.contrib import admin
from django.contrib.auth.models import Group, Permission, User
from django.core.exceptions import PermissionDenied
from django.core.management import call_command, CommandError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase
from django.urls import reverse

from .admin_navigation import build_admin_navigation
from .admin_roles import ADMIN_ROLE_BY_NAME, ADMIN_ROLES, sync_admin_roles
from .models import (
    Badge,
    Category,
    ConsentRecord,
    MemeOfWeek,
    MissionActivity,
    PageView,
    PersonalDataCleanupRun,
    PointTransaction,
    PostDocument,
    PostImage,
    Posts,
    UserBadge,
    UserDigitalReward,
    UserMissionProgress,
    UserProfile,
)
from valentine.models import Profile


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

    def test_normal_sync_preserves_intentionally_removed_baseline_permission(self):
        sync_admin_roles(reset=True)
        group = Group.objects.get(name="Автори")
        baseline = Permission.objects.get(
            content_type__app_label="blog",
            codename="add_posts",
        )
        group.permissions.remove(baseline)

        sync_admin_roles()

        self.assertFalse(group.permissions.filter(pk=baseline.pk).exists())

        sync_admin_roles(reset=True)
        self.assertTrue(group.permissions.filter(pk=baseline.pk).exists())

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
        self.assertNotIn("Официални известия", titles)

    def test_empty_sections_are_removed_for_moderator(self):
        navigation = self._navigation_for_role("Модератори")
        sections = _section_titles(navigation)
        titles = _visible_titles(navigation)

        self.assertIn("Ангажираност на потребителите", sections)
        self.assertIn("Коментари", titles)
        self.assertNotIn("Геймификация", sections)
        self.assertNotIn("Валентинки", sections)

    def test_site_manager_does_not_see_superuser_only_cleanup_history(self):
        navigation = self._navigation_for_role("Управление на сайта")
        titles = _visible_titles(navigation)

        self.assertIn("Прегледи на страници", titles)
        self.assertNotIn("История на почистването", titles)

    def test_raw_permission_does_not_expose_model_blocked_by_model_admin(self):
        user = User.objects.create_user(
            username="nav-stale-cleanup-permission",
            password="test-password",
            is_staff=True,
        )
        user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="blog",
                codename="view_personaldatacleanuprun",
            )
        )
        request = RequestFactory().get("/admin/")
        request.user = user

        self.assertNotIn(
            "История на почистването",
            _visible_titles(build_admin_navigation(request)),
        )

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


class LeastPrivilegeRoleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        sync_admin_roles(reset=True)

    def _permissions(self, role_name):
        return {
            f"{permission.content_type.app_label}.{permission.codename}"
            for permission in Group.objects.get(name=role_name).permissions.select_related(
                "content_type"
            )
        }

    def test_editor_cannot_delete_legal_or_schedule_history(self):
        permissions = self._permissions("Редактори")

        self.assertIn("blog.change_scheduleimport", permissions)
        self.assertNotIn("blog.delete_scheduleimport", permissions)
        self.assertNotIn("blog.delete_privacypolicy", permissions)
        self.assertNotIn("blog.delete_termsofservice", permissions)
        self.assertIn("blog.can_edit_users_post", permissions)

    def test_gamification_history_is_read_only(self):
        permissions = self._permissions("Мениджъри на геймификацията")

        for codename in (
            "delete_userbadge",
            "change_pointtransaction",
            "delete_pointtransaction",
            "change_usermissionprogress",
            "delete_usermissionprogress",
            "change_missionactivity",
            "delete_missionactivity",
            "delete_badge",
            "delete_mission",
            "delete_digitalreward",
        ):
            with self.subTest(codename=codename):
                self.assertNotIn(f"blog.{codename}", permissions)
        self.assertNotIn("blog.view_notification", permissions)
        self.assertIn("blog.view_gamificationnotification", permissions)

    def test_site_manager_audit_and_privacy_records_are_read_only(self):
        permissions = self._permissions("Управление на сайта")

        self.assertIn("blog.view_pageview", permissions)
        self.assertIn("blog.view_consentrecord", permissions)
        self.assertNotIn("blog.delete_pageview", permissions)
        self.assertNotIn("blog.delete_consentrecord", permissions)
        self.assertNotIn("blog.view_personaldatacleanuprun", permissions)

    def test_valentine_codes_require_separate_role(self):
        manager_permissions = self._permissions("Мениджъри на Валентинки")
        code_permissions = self._permissions("Достъп до Valentine кодове")

        self.assertNotIn("valentine.view_profile_codes", manager_permissions)
        self.assertIn("valentine.view_profile_codes", code_permissions)
        self.assertIn("valentine.view_profile", code_permissions)
        self.assertNotIn("valentine.change_profile", code_permissions)

    def test_no_standard_role_can_manage_users_groups_or_superusers(self):
        forbidden = {
            "auth.add_user",
            "auth.change_user",
            "auth.delete_user",
            "auth.add_group",
            "auth.change_group",
            "auth.delete_group",
        }
        for role in ADMIN_ROLES:
            with self.subTest(role=role.name):
                self.assertTrue(forbidden.isdisjoint(role.permissions))


class ImmutableAdminRecordTests(TestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser(
            username="permission-root",
            email="root@example.com",
            password="test-password",
        )
        self.request = RequestFactory().get("/admin/")
        self.request.user = self.superuser

    def test_sensitive_history_cannot_be_deleted_even_with_broad_permissions(self):
        models = (
            ConsentRecord,
            PageView,
            PersonalDataCleanupRun,
            PointTransaction,
            UserBadge,
            UserMissionProgress,
            MissionActivity,
        )
        for model in models:
            with self.subTest(model=model.__name__):
                model_admin = admin.site._registry[model]
                self.assertFalse(model_admin.has_delete_permission(self.request))


class PrivilegeEscalationProtectionTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username="permission-manager",
            password="test-password",
            is_staff=True,
        )
        self.staff.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="auth",
                codename="change_user",
            ),
            Permission.objects.get(
                content_type__app_label="auth",
                codename="change_group",
            ),
        )
        self.request = RequestFactory().get("/admin/")
        self.request.user = self.staff

    def test_non_superuser_cannot_edit_users_even_with_raw_change_permission(self):
        self.assertFalse(
            admin.site._registry[User].has_change_permission(self.request, self.staff)
        )

    def test_non_superuser_cannot_edit_groups_even_with_raw_change_permission(self):
        self.assertFalse(
            admin.site._registry[Group].has_change_permission(
                self.request,
                Group.objects.get(name="Автори"),
            )
        )


class AdminActionPermissionTests(TestCase):
    def setUp(self):
        sync_admin_roles(reset=True)
        self.superuser = User.objects.create_superuser(
            username="action-root",
            email="action-root@example.com",
            password="test-password",
        )
        self.request = RequestFactory().get("/admin/")
        self.request.user = self.superuser

    def test_every_registered_action_declares_its_required_permission(self):
        for model, model_admin in admin.site._registry.items():
            for action, action_name, _ in model_admin.get_actions(self.request).values():
                with self.subTest(model=model.__name__, action=action_name):
                    self.assertTrue(
                        hasattr(action, "allowed_permissions"),
                        f"{model.__name__}.{action_name} has no explicit permission",
                    )

    def test_view_only_user_cannot_call_profile_mutation_directly(self):
        code_only = User.objects.create_user(
            username="action-code-viewer",
            password="test-password",
            is_staff=True,
        )
        code_only.groups.add(Group.objects.get(name="Достъп до Valentine кодове"))
        request = RequestFactory().post("/admin/valentine/profile/")
        request.user = code_only
        profile_admin = admin.site._registry[Profile]

        with self.assertRaises(PermissionDenied):
            profile_admin.deactivate_profiles(request, Profile.objects.none())


class RoleHttpAccessMatrixTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        sync_admin_roles(reset=True)

    def _login_as(self, role_name, username):
        user = User.objects.create_user(
            username=username,
            password="test-password",
            is_staff=True,
        )
        user.groups.add(Group.objects.get(name=role_name))
        self.client.force_login(user)
        return user

    def test_author_can_open_posts_but_not_gamification(self):
        self._login_as("Автори", "http-author")

        self.assertEqual(
            self.client.get(reverse("admin:blog_posts_changelist")).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(reverse("admin:blog_badge_changelist")).status_code,
            403,
        )

    def test_moderator_can_open_comments_but_not_posts(self):
        self._login_as("Модератори", "http-moderator")

        self.assertEqual(
            self.client.get(reverse("admin:blog_comments_changelist")).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(reverse("admin:blog_posts_changelist")).status_code,
            403,
        )

    def test_gamification_manager_cannot_open_official_notifications(self):
        self._login_as("Мениджъри на геймификацията", "http-gamification")

        self.assertEqual(
            self.client.get(reverse("admin:blog_badge_changelist")).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(reverse("admin:blog_notification_changelist")).status_code,
            403,
        )

    def test_site_manager_cannot_open_superuser_cleanup_history(self):
        self._login_as("Управление на сайта", "http-site-manager")

        self.assertEqual(
            self.client.get(reverse("admin:blog_pageview_changelist")).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(
                reverse("admin:blog_personaldatacleanuprun_changelist")
            ).status_code,
            403,
        )


class AuthorObjectScopeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        sync_admin_roles(reset=True)
        cls.author = User.objects.create_user(
            username="scope-author",
            password="test-password",
            is_staff=True,
        )
        cls.author.groups.add(Group.objects.get(name="Автори"))
        cls.other_author = User.objects.create_user(
            username="scope-other-author",
            password="test-password",
        )
        cls.editor = User.objects.create_user(
            username="scope-editor",
            password="test-password",
            is_staff=True,
        )
        cls.editor.groups.add(Group.objects.get(name="Редактори"))
        category = Category.objects.create(full_name="Scope", short_name="scope")
        cls.own_post = Posts.objects.create(
            title="Own post",
            category=category,
            hook="Own",
            content="Own",
            author=cls.author,
        )
        cls.foreign_post = Posts.objects.create(
            title="Foreign post",
            category=category,
            hook="Foreign",
            content="Foreign",
            author=cls.other_author,
        )
        cls.own_image, cls.foreign_image = PostImage.objects.bulk_create(
            [
                PostImage(post=cls.own_post, image="posts/own.jpg"),
                PostImage(post=cls.foreign_post, image="posts/foreign.jpg"),
            ]
        )
        cls.own_document = PostDocument.objects.create(
            post=cls.own_post,
            file="posts/own.pdf",
        )
        cls.foreign_document = PostDocument.objects.create(
            post=cls.foreign_post,
            file="posts/foreign.pdf",
        )

    def _request(self, user):
        request = RequestFactory().get("/admin/")
        request.user = user
        return request

    def test_author_sees_only_attachments_from_own_posts(self):
        request = self._request(self.author)

        image_ids = set(
            admin.site._registry[PostImage]
            .get_queryset(request)
            .values_list("pk", flat=True)
        )
        document_ids = set(
            admin.site._registry[PostDocument]
            .get_queryset(request)
            .values_list("pk", flat=True)
        )

        self.assertEqual(image_ids, {self.own_image.pk})
        self.assertEqual(document_ids, {self.own_document.pk})

    def test_author_cannot_edit_foreign_post_but_editor_can(self):
        post_admin = admin.site._registry[Posts]

        self.assertTrue(
            post_admin.has_change_permission(
                self._request(self.author),
                self.own_post,
            )
        )
        self.assertFalse(
            post_admin.has_change_permission(
                self._request(self.author),
                self.foreign_post,
            )
        )
        self.assertTrue(
            post_admin.has_change_permission(
                self._request(self.editor),
                self.foreign_post,
            )
        )

    def test_gamification_manager_cannot_edit_profile_preferences_directly(self):
        manager = User.objects.create_user(
            username="scope-gamification",
            password="test-password",
            is_staff=True,
        )
        manager.groups.add(Group.objects.get(name="Мениджъри на геймификацията"))
        request = self._request(manager)
        readonly = set(admin.site._registry[UserProfile].get_readonly_fields(request))

        self.assertTrue(
            {field.name for field in UserProfile._meta.fields}.issubset(readonly)
        )

    def test_moderator_cannot_replace_submitted_meme_image(self):
        request = self._request(
            User.objects.create_user(
                username="scope-moderator",
                password="test-password",
                is_staff=True,
            )
        )
        request.user.groups.add(Group.objects.get(name="Модератори"))

        self.assertIn(
            "image",
            admin.site._registry[MemeOfWeek].get_readonly_fields(request),
        )

    def test_existing_digital_reward_assignment_keeps_its_identity(self):
        manager = User.objects.create_user(
            username="scope-reward-manager",
            password="test-password",
            is_staff=True,
        )
        manager.groups.add(Group.objects.get(name="Мениджъри на геймификацията"))
        request = self._request(manager)
        readonly = set(
            admin.site._registry[UserDigitalReward].get_readonly_fields(
                request,
                obj=UserDigitalReward(),
            )
        )

        self.assertTrue({"user", "reward", "source_badge"}.issubset(readonly))
        self.assertNotIn("selected", readonly)


class ValentineCodeIsolationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        sync_admin_roles(reset=True)

    def _user(self, *, with_codes=False):
        user = User.objects.create_user(
            username=f"valentine-code-{int(with_codes)}",
            password="test-password",
            is_staff=True,
        )
        groups = [Group.objects.get(name="Мениджъри на Валентинки")]
        if with_codes:
            groups.append(Group.objects.get(name="Достъп до Valentine кодове"))
        user.groups.add(*groups)
        return user

    def test_profile_code_field_is_hidden_without_sensitive_permission(self):
        model_admin = admin.site._registry[Profile]
        basic_request = RequestFactory().get("/admin/")
        basic_request.user = self._user(with_codes=False)
        senior_request = RequestFactory().get("/admin/")
        senior_request.user = self._user(with_codes=True)

        self.assertIn("code", model_admin.get_exclude(basic_request))
        self.assertNotIn("code", model_admin.get_exclude(senior_request))
        self.assertIn("code", model_admin.get_readonly_fields(senior_request))

    def test_code_only_role_gets_code_actions_without_general_profile_actions(self):
        code_only = User.objects.create_user(
            username="valentine-code-only-actions",
            password="test-password",
            is_staff=True,
        )
        code_only.groups.add(Group.objects.get(name="Достъп до Valentine кодове"))
        request = RequestFactory().get("/admin/")
        request.user = code_only

        actions = admin.site._registry[Profile].get_actions(request)

        self.assertIn("regenerate_access_codes", actions)
        self.assertIn("export_selected_profiles_pdf", actions)
        self.assertNotIn("reset_pins", actions)
        self.assertNotIn("deactivate_profiles", actions)

    def test_csv_template_contains_code_only_for_sensitive_role(self):
        basic = self._user(with_codes=False)
        self.client.force_login(basic)
        response = self.client.get(reverse("admin:valentine_profile_import_template"))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("code", response.content.decode("utf-8-sig").splitlines()[0])

        senior = self._user(with_codes=True)
        self.client.force_login(senior)
        response = self.client.get(reverse("admin:valentine_profile_import_template"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("code", response.content.decode("utf-8-sig").splitlines()[0])

    def test_csv_import_rejects_supplied_code_without_sensitive_permission(self):
        user = self._user(with_codes=False)
        self.client.force_login(user)
        csv_file = SimpleUploadedFile(
            "profiles.csv",
            "first_name;last_name;class;email;code\nTest;Student;8A;;ABC123\n".encode(
                "utf-8"
            ),
            content_type="text/csv",
        )

        response = self.client.post(
            reverse("admin:valentine_profile_import_csv"),
            {"academic_year_start": 2026, "csv_file": csv_file},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Нямаш право да задаваш Valentine кодове")
        self.assertFalse(Profile.objects.filter(first_name="Test").exists())
