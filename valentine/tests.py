import re
import uuid
from datetime import date, datetime, timedelta, timezone as datetime_timezone
from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .admin import (
    ArchivedValentinesAdmin,
    ProfileAdmin,
    RegisterAdmin,
    ValentineCampaignAdmin,
    ValentinesAdmin,
)
from .models import (
    ArchivedValentine,
    Profile,
    Register,
    Settings,
    ValentineCampaign,
    ValentineLoginAttempt,
    ValentineModerationEvent,
    Valenitines,
)
from .profile_import import (
    ProfileImportError,
    apply_profile_import,
    build_profile_import_plan,
)
from .profile_pdf import group_profiles_by_class
from .views import (
    LOGIN_STATUS_INVALID,
    _authenticate_login,
    _client_ip,
)


@override_settings(DEBUG=False)
class ValentinePlatformTests(TestCase):
    def setUp(self):
        self.sender = Profile.objects.create(
            code="SEND01",
            first_name="Иван",
            last_name="Иванов",
            clas="10A",
            email="ivan@example.com",
        )
        self.recipient = Profile.objects.create(
            code="RECV01",
            first_name="Мария",
            last_name="Петрова",
            clas="10B",
            email="maria@example.com",
        )
        self.same_name_recipient = Profile.objects.create(
            code="RECV02",
            first_name="Мария",
            last_name="Петрова",
            clas="11A",
            email="maria2@example.com",
        )
        reveal_at = timezone.now() + timedelta(days=3)
        self.campaign = ValentineCampaign.objects.create(
            year=reveal_at.year,
        )
        self.platform_settings = Settings.objects.create(
            active_campaign=self.campaign,
            reveal_date=date.today() + timedelta(days=3),
            reveal_at=reveal_at,
            sending_starts_at=timezone.now() - timedelta(days=1),
            sending_ends_at=timezone.now() + timedelta(days=2),
            daily_limit=10,
            cooldown_seconds=0,
        )

    def login(self, code="SEND01"):
        return self.client.post(reverse("valentine:login"), {"code": code})

    def reveal_campaign(self):
        self.platform_settings.sending_ends_at = (
            timezone.now() - timedelta(minutes=2)
        )
        self.platform_settings.reveal_at = (
            timezone.now() - timedelta(minutes=1)
        )
        self.platform_settings.reveal_date = timezone.localdate()
        self.platform_settings.save()

    def test_login_creates_hardened_cookie(self):
        response = self.login()

        self.assertRedirects(response, reverse("valentine:home"))
        cookie = response.cookies["authv"]
        self.assertTrue(cookie["httponly"])
        self.assertTrue(cookie["secure"])
        self.assertEqual(cookie["samesite"], "Lax")

    def test_external_next_url_is_not_allowed(self):
        response = self.client.post(
            f"{reverse('valentine:login')}?next=https://example.org/",
            {"code": "SEND01"},
        )

        self.assertRedirects(response, reverse("valentine:home"))

    def test_profile_can_be_deactivated_without_losing_history(self):
        valentine = Valenitines.objects.create(
            profile=self.recipient,
            sender_profile=self.sender,
            campaign=self.campaign,
            content="Историята трябва да остане",
        )
        self.login()
        active_session = self.sender.sessions.get(revoked=False)
        request = RequestFactory().post("/admin/valentine/profile/")
        request.user = get_user_model().objects.create_superuser(
            username="profile-admin",
            email="profiles@example.com",
            password="test-password",
        )
        model_admin = ProfileAdmin(Profile, AdminSite())

        with patch.object(model_admin, "message_user"):
            model_admin.deactivate_profiles(
                request,
                Profile.objects.filter(pk=self.sender.pk),
            )

        self.sender.refresh_from_db()
        active_session.refresh_from_db()
        valentine.refresh_from_db()
        self.assertFalse(self.sender.is_active)
        self.assertTrue(active_session.revoked)
        self.assertTrue(Profile.objects.filter(pk=self.sender.pk).exists())
        self.assertEqual(valentine.sender_profile, self.sender)
        self.assertEqual(valentine.content, "Историята трябва да остане")
        self.assertFalse(model_admin.has_delete_permission(request))

        home_response = self.client.get(reverse("valentine:home"))
        self.assertRedirects(
            home_response,
            f"{reverse('valentine:login')}?next={reverse('valentine:home')}",
        )
        inactive_login = self.login()
        self.assertEqual(inactive_login.status_code, 200)
        self.assertContains(
            inactive_login,
            "Кодът е невалиден или е изтекъл.",
        )

        with patch.object(model_admin, "message_user"):
            model_admin.reactivate_profiles(
                request,
                Profile.objects.filter(pk=self.sender.pk),
            )
        self.sender.refresh_from_db()
        self.assertTrue(self.sender.is_active)
        self.assertRedirects(self.login(), reverse("valentine:home"))

    def test_unauthenticated_user_cannot_send(self):
        response = self.client.post(
            reverse("valentine:home"),
            {
                "recipient": self.recipient.pk,
                "content": "Мило послание",
                "theme": "classic",
                "is_anonymous": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Valenitines.objects.count(), 0)

    def test_recipient_is_selected_by_uuid_not_name(self):
        self.login()
        response = self.client.post(
            reverse("valentine:home"),
            {
                "recipient": self.same_name_recipient.pk,
                "content": "Успех и много усмивки!",
                "theme": "bloom",
                "is_anonymous": "on",
            },
        )

        self.assertRedirects(response, reverse("valentine:home"))
        valentine = Valenitines.objects.get()
        self.assertEqual(valentine.profile, self.same_name_recipient)
        self.assertEqual(valentine.sender_profile, self.sender)
        self.assertTrue(valentine.is_anonymous)

    def test_daily_limit_is_enforced_on_server(self):
        self.platform_settings.daily_limit = 1
        self.platform_settings.save()
        Valenitines.objects.create(
            profile=self.recipient,
            sender_profile=self.sender,
            campaign=self.campaign,
            content="Първо послание",
        )
        self.login()

        response = self.client.post(
            reverse("valentine:home"),
            {
                "recipient": self.same_name_recipient.pk,
                "content": "Второ послание",
                "theme": "classic",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Достигна дневния лимит")
        self.assertEqual(Valenitines.objects.count(), 1)

    def test_inbox_is_locked_before_reveal(self):
        Valenitines.objects.create(
            profile=self.sender,
            sender_profile=self.recipient,
            campaign=self.campaign,
            content="Тайно послание",
        )
        self.login()

        response = self.client.get(reverse("valentine:inbox"))

        self.assertContains(response, "Все още са запечатани")
        self.assertNotContains(response, "Тайно послание")

    def test_inbox_reveals_messages_after_reveal_time(self):
        self.platform_settings.sending_ends_at = timezone.now() - timedelta(
            minutes=2
        )
        self.platform_settings.reveal_at = timezone.now() - timedelta(minutes=1)
        self.platform_settings.save()
        Valenitines.objects.create(
            profile=self.sender,
            sender_profile=self.recipient,
            campaign=self.campaign,
            content="Вече разкрито послание",
            is_anonymous=False,
        )
        self.login()

        response = self.client.get(reverse("valentine:inbox"))

        self.assertContains(response, "Вече разкрито послание")
        self.assertContains(response, self.recipient.full_name)

    def test_report_can_only_target_received_message(self):
        received = Valenitines.objects.create(
            profile=self.sender,
            sender_profile=self.recipient,
            campaign=self.campaign,
            content="Неподходящо",
        )
        foreign = Valenitines.objects.create(
            profile=self.same_name_recipient,
            sender_profile=self.recipient,
            campaign=self.campaign,
            content="Чуждо",
        )
        self.reveal_campaign()
        self.login()

        response = self.client.post(
            reverse("valentine:report", args=(received.pk,)),
            {"reason": "inappropriate"},
        )
        self.assertRedirects(response, reverse("valentine:inbox"))
        received.refresh_from_db()
        self.assertTrue(received.is_reported)
        self.assertEqual(received.moderation_status, "pending")

        response = self.client.post(
            reverse("valentine:report", args=(foreign.pk,)),
            {"reason": "other"},
        )
        self.assertEqual(response.status_code, 404)

    def test_message_cannot_be_reported_before_reveal(self):
        received = Valenitines.objects.create(
            profile=self.sender,
            sender_profile=self.recipient,
            campaign=self.campaign,
            content="Все още запечатано",
        )
        self.login()

        response = self.client.post(
            reverse("valentine:report", args=(received.pk,)),
            {"reason": Valenitines.ReportReason.OTHER},
        )

        self.assertRedirects(response, reverse("valentine:inbox"))
        received.refresh_from_db()
        self.assertEqual(
            received.moderation_status,
            Valenitines.ModerationStatus.CLEAR,
        )
        self.assertFalse(received.is_reported)
        self.assertFalse(
            ValentineModerationEvent.objects.filter(
                dispatch_id=received.dispatch_id
            ).exists()
        )

    def test_group_message_reaches_everyone_in_selected_class(self):
        classmate = Profile.objects.create(
            code="CLASS2",
            first_name="Никол",
            last_name="Георгиева",
            clas="10B",
            email="nikol@example.com",
        )
        self.login()

        response = self.client.post(
            reverse("valentine:home"),
            {
                "target_type": "class",
                "target_class": "10B",
                "content": "Прекрасен празник на целия клас!",
                "theme": "candy",
                "is_anonymous": "on",
            },
        )

        self.assertRedirects(response, reverse("valentine:home"))
        deliveries = Valenitines.objects.filter(
            content="Прекрасен празник на целия клас!"
        )
        self.assertSetEqual(
            set(deliveries.values_list("profile_id", flat=True)),
            {self.recipient.pk, classmate.pk},
        )
        self.assertEqual(
            deliveries.values("dispatch_id").distinct().count(),
            1,
        )
        self.assertTrue(
            all(
                delivery.audience_type == "class"
                and delivery.audience_label == "10Б клас"
                for delivery in deliveries
            )
        )

    def test_group_message_reaches_all_teachers(self):
        teacher_one = Profile.objects.create(
            code="TEAC01",
            first_name="Елена",
            last_name="Николова",
            clas="U",
            email="elena@example.com",
        )
        teacher_two = Profile.objects.create(
            code="TEAC02",
            first_name="Петър",
            last_name="Димитров",
            clas="U",
            email="petar@example.com",
        )
        self.login()

        response = self.client.post(
            reverse("valentine:home"),
            {
                "target_type": "teachers",
                "content": "Благодарим ви, учители!",
                "theme": "bloom",
            },
        )

        self.assertRedirects(response, reverse("valentine:home"))
        deliveries = Valenitines.objects.filter(
            content="Благодарим ви, учители!"
        )
        self.assertSetEqual(
            set(deliveries.values_list("profile_id", flat=True)),
            {teacher_one.pk, teacher_two.pk},
        )
        self.assertEqual(
            deliveries.values("dispatch_id").distinct().count(),
            1,
        )

    def test_individual_teacher_can_be_selected_and_receive_message(self):
        teacher = Profile.objects.create(
            code="TEAC03",
            first_name="Ралица",
            last_name="Тодорова",
            clas="U",
            email="ralitsa@example.com",
        )
        self.login()

        page = self.client.get(reverse("valentine:home"))
        self.assertNotContains(page, '<option value="U"')
        self.assertContains(page, 'data-teacher-count="1"')

        teacher_options = self.client.get(
            reverse("valentine:students", args=("U",))
        )
        self.assertEqual(teacher_options.status_code, 200)
        self.assertEqual(
            teacher_options.json()["students"],
            [{"id": str(teacher.pk), "name": teacher.full_name}],
        )

        response = self.client.post(
            reverse("valentine:home"),
            {
                "target_type": "individual",
                "target_class": "U",
                "recipient": teacher.pk,
                "content": "Благодаря за вдъхновението!",
                "theme": "classic",
            },
        )

        self.assertRedirects(response, reverse("valentine:home"))
        delivery = Valenitines.objects.get(
            content="Благодаря за вдъхновението!"
        )
        self.assertEqual(delivery.profile, teacher)
        self.assertEqual(
            delivery.audience_type,
            Valenitines.AudienceType.INDIVIDUAL,
        )
        self.assertEqual(delivery.audience_label, teacher.full_name)

    def test_inactive_profile_cannot_be_selected_as_recipient(self):
        self.recipient.is_active = False
        self.recipient.save(update_fields=["is_active"])
        active_classmate = Profile.objects.create(
            code="CLASS3",
            first_name="Десислава",
            last_name="Маринова",
            clas="10B",
            email="desislava@example.com",
        )
        self.login()

        recipient_options = self.client.get(
            reverse("valentine:students", args=("10B",))
        )
        self.assertEqual(recipient_options.status_code, 200)
        self.assertEqual(
            recipient_options.json()["students"],
            [
                {
                    "id": str(active_classmate.pk),
                    "name": active_classmate.full_name,
                }
            ],
        )

        direct_response = self.client.post(
            reverse("valentine:home"),
            {
                "target_type": "individual",
                "target_class": "10B",
                "recipient": self.recipient.pk,
                "content": "Не трябва да бъде доставено",
                "theme": "classic",
            },
        )
        self.assertEqual(direct_response.status_code, 200)
        self.assertFalse(
            Valenitines.objects.filter(
                content="Не трябва да бъде доставено"
            ).exists()
        )

        group_response = self.client.post(
            reverse("valentine:home"),
            {
                "target_type": "class",
                "target_class": "10B",
                "content": "Само до активните профили",
                "theme": "classic",
            },
        )
        self.assertRedirects(group_response, reverse("valentine:home"))
        deliveries = Valenitines.objects.filter(
            content="Само до активните профили"
        )
        self.assertSetEqual(
            set(deliveries.values_list("profile_id", flat=True)),
            {active_classmate.pk},
        )

    def test_class_migration_preserves_historical_campaign_class(self):
        self.recipient.clas = "11A"
        self.recipient.academic_year_start = 2025
        self.recipient.save(
            update_fields=("clas", "academic_year_start")
        )
        # Simulate a legacy bulk import which bypassed Profile.save().
        Profile.objects.filter(pk=self.recipient.pk).update(
            clas="11А\u00a0"
        )
        self.recipient.refresh_from_db()
        self.assertEqual(self.recipient.clas, "11А\u00a0")
        teacher = Profile.objects.create(
            code="TEAC04",
            first_name="Милена",
            last_name="Илиева",
            clas="U",
            email="milena@example.com",
        )
        historical = Valenitines.objects.create(
            profile=self.recipient,
            sender_profile=self.sender,
            campaign=self.campaign,
            content="Исторически клас",
        )
        self.assertEqual(
            historical.recipient_class_snapshot,
            "11A",
        )
        migration_admin = get_user_model().objects.create_superuser(
            username="class-admin",
            email="classes@example.com",
            password="test-password",
        )
        self.client.force_login(migration_admin)
        changelist_url = reverse("admin:valentine_profile_changelist")
        selected_profiles = [str(self.recipient.pk), str(teacher.pk)]

        preview = self.client.post(
            changelist_url,
            {
                "action": "promote_to_next_academic_year",
                "_selected_action": selected_profiles,
                "select_across": "0",
                "index": "0",
            },
        )
        self.assertEqual(preview.status_code, 200)
        self.assertContains(preview, "12А клас")
        self.assertContains(preview, "2026/2027")
        self.assertContains(preview, "Учител – не се променя")

        confirmed = self.client.post(
            changelist_url,
            {
                "action": "promote_to_next_academic_year",
                "_selected_action": selected_profiles,
                "select_across": "0",
                "apply": "yes",
                "target_academic_year_start": "2026",
            },
        )
        self.assertEqual(confirmed.status_code, 302)

        self.recipient.refresh_from_db()
        teacher.refresh_from_db()
        historical.refresh_from_db()
        self.assertEqual(self.recipient.clas, "12A")
        self.assertEqual(self.recipient.academic_year_start, 2026)
        self.assertEqual(teacher.clas, "U")
        self.assertEqual(
            historical.recipient_class_snapshot,
            "11A",
        )

        campaign_admin = ValentineCampaignAdmin(
            ValentineCampaign,
            AdminSite(),
        )
        statistics = campaign_admin.build_statistics(self.campaign)
        class_labels = {
            row["label"] for row in statistics["class_rows"]
        }
        self.assertIn("11А клас", class_labels)
        self.assertNotIn("12А клас", class_labels)

    def test_admin_can_preview_and_confirm_student_csv_import(self):
        self.sender.is_active = False
        self.sender.save(update_fields=("is_active",))
        admin_user = get_user_model().objects.create_superuser(
            username="csv-admin",
            email="csv-admin@example.com",
            password="test-password",
        )
        self.client.force_login(admin_user)
        import_url = reverse("admin:valentine_profile_import_csv")
        csv_content = (
            "Име;Фамилия;Клас;Имейл;Код\n"
            "Александра;Георгиева;8Б;;\n"
            "Иван;Иванов;11А;ivan-new@example.com;SEND01\n"
            "Мария;Петрова;9В;;RECV01\n"
        )
        upload = SimpleUploadedFile(
            "students.csv",
            csv_content.encode("cp1251"),
            content_type="text/csv",
        )

        preview = self.client.post(
            import_url,
            {
                "academic_year_start": "2026",
                "csv_file": upload,
            },
        )

        self.assertEqual(preview.status_code, 200)
        self.assertContains(preview, "Предварителен преглед – 3 ученици")
        self.assertContains(preview, "Нов профил")
        self.assertContains(preview, "Възстановяване")
        self.assertFalse(
            Profile.objects.filter(
                first_name="Александра",
                last_name="Георгиева",
            ).exists()
        )
        import_token = preview.context["import_token"]
        self.assertTrue(import_token)

        confirmed = self.client.post(
            import_url,
            {
                "confirm": "yes",
                "import_token": import_token,
            },
        )

        self.assertRedirects(
            confirmed,
            reverse("admin:valentine_profile_changelist"),
        )
        imported = Profile.objects.get(
            first_name="Александра",
            last_name="Георгиева",
        )
        self.assertEqual(imported.clas, "8B")
        self.assertEqual(imported.academic_year_start, 2026)
        self.assertEqual(imported.email, "")
        self.assertEqual(len(imported.code), 6)
        self.sender.refresh_from_db()
        self.assertTrue(self.sender.is_active)
        self.assertEqual(self.sender.clas, "11A")
        self.assertEqual(self.sender.email, "ivan-new@example.com")
        self.assertEqual(self.sender.academic_year_start, 2026)
        self.recipient.refresh_from_db()
        self.assertEqual(self.recipient.clas, "9V")
        self.assertEqual(self.recipient.email, "maria@example.com")

    def test_invalid_csv_import_does_not_write_partial_data(self):
        admin_user = get_user_model().objects.create_superuser(
            username="invalid-csv-admin",
            email="invalid-csv@example.com",
            password="test-password",
        )
        self.client.force_login(admin_user)
        profile_count = Profile.objects.count()
        upload = SimpleUploadedFile(
            "students.csv",
            (
                "first_name,last_name,class,email\n"
                "Валидна,Ученичка,8А,valid@example.com\n"
                "Грешна,Ученичка,13Я,not-an-email\n"
            ).encode("utf-8"),
            content_type="text/csv",
        )

        response = self.client.post(
            reverse("admin:valentine_profile_import_csv"),
            {
                "academic_year_start": "2026",
                "csv_file": upload,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Импортът не е записан")
        self.assertContains(response, "Ред 3")
        self.assertEqual(Profile.objects.count(), profile_count)
        self.assertFalse(response.context["import_token"])

    def test_admin_can_download_active_profile_codes_as_pdf(self):
        teacher = Profile.objects.create(
            code="PDFT01",
            first_name="Анелия",
            last_name="Тодорова",
            clas="U",
            email="anelia@example.com",
        )
        admin_user = get_user_model().objects.create_superuser(
            username="pdf-admin",
            email="pdf-admin@example.com",
            password="test-password",
        )
        self.client.force_login(admin_user)

        changelist = self.client.get(
            reverse("admin:valentine_profile_changelist")
        )
        self.assertContains(changelist, "Всички кодове в PDF")
        self.assertContains(changelist, "Импорт от CSV")

        response = self.client.get(
            reverse("admin:valentine_profile_active_codes_pdf")
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn(
            "valentine-active-codes-",
            response["Content-Disposition"],
        )
        self.assertTrue(response.content.startswith(b"%PDF-"))
        self.assertGreater(len(response.content), 1000)
        self.assertIn("private", response["Cache-Control"])
        self.assertIn("no-store", response["Cache-Control"])
        grouped_codes = [
            class_code
            for class_code, _profiles in group_profiles_by_class(
                Profile.objects.filter(is_active=True)
            )
        ]
        self.assertEqual(grouped_codes, ["10A", "10B", "11A", "U"])
        self.assertIn(
            teacher,
            dict(
                group_profiles_by_class(
                    Profile.objects.filter(is_active=True)
                )
            )["U"],
        )
        page_count = len(
            re.findall(
                rb"/Type\s*/Page(?!s)\b",
                response.content,
            )
        )
        self.assertEqual(page_count, 4)

    def test_group_daily_limit_is_enforced_per_dispatch(self):
        Profile.objects.create(
            code="CLASS2",
            first_name="Никол",
            last_name="Георгиева",
            clas="10B",
            email="nikol@example.com",
        )
        Profile.objects.create(
            code="TEAC01",
            first_name="Елена",
            last_name="Николова",
            clas="U",
            email="elena@example.com",
        )
        self.platform_settings.group_daily_limit = 1
        self.platform_settings.save()
        self.login()

        first = self.client.post(
            reverse("valentine:home"),
            {
                "target_type": "class",
                "target_class": "10B",
                "content": "До целия клас",
                "theme": "classic",
            },
        )
        self.assertRedirects(first, reverse("valentine:home"))
        delivery_count = Valenitines.objects.count()

        second = self.client.post(
            reverse("valentine:home"),
            {
                "target_type": "teachers",
                "content": "До всички учители",
                "theme": "classic",
            },
        )

        self.assertEqual(second.status_code, 200)
        self.assertContains(second, "Достигна дневния лимит")
        self.assertEqual(Valenitines.objects.count(), delivery_count)

    def test_sender_can_edit_entire_dispatch_before_reveal(self):
        dispatch_id = uuid.uuid4()
        first = Valenitines.objects.create(
            profile=self.recipient,
            sender_profile=self.sender,
            campaign=self.campaign,
            dispatch_id=dispatch_id,
            audience_type="class",
            audience_label="10Б клас",
            content="Първоначално послание",
        )
        second = Valenitines.objects.create(
            profile=self.same_name_recipient,
            sender_profile=self.sender,
            campaign=self.campaign,
            dispatch_id=dispatch_id,
            audience_type="class",
            audience_label="10Б клас",
            content="Първоначално послание",
        )
        self.login()

        response = self.client.post(
            reverse("valentine:edit", args=(dispatch_id,)),
            {
                "content": "Редактирано послание",
                "theme": "midnight",
                "is_anonymous": "on",
            },
        )

        self.assertRedirects(response, reverse("valentine:outbox"))
        first.refresh_from_db()
        second.refresh_from_db()
        for delivery in (first, second):
            self.assertEqual(delivery.content, "Редактирано послание")
            self.assertEqual(delivery.theme, "midnight")
            self.assertTrue(delivery.is_anonymous)
            self.assertIsNotNone(delivery.edited_at)

    def test_dispatch_cannot_be_edited_after_reveal(self):
        dispatch = Valenitines.objects.create(
            profile=self.recipient,
            sender_profile=self.sender,
            campaign=self.campaign,
            content="Заключено послание",
        )
        self.platform_settings.sending_ends_at = timezone.now() - timedelta(
            minutes=2
        )
        self.platform_settings.reveal_at = timezone.now() - timedelta(minutes=1)
        self.platform_settings.save()
        self.login()

        response = self.client.post(
            reverse("valentine:edit", args=(dispatch.dispatch_id,)),
            {
                "content": "Опит за промяна",
                "theme": "sunset",
            },
        )

        self.assertRedirects(response, reverse("valentine:outbox"))
        dispatch.refresh_from_db()
        self.assertEqual(dispatch.content, "Заключено послание")
        self.assertIsNone(dispatch.edited_at)

    def test_admin_only_sees_moderated_messages_and_cannot_delete(self):
        clear = Valenitines.objects.create(
            profile=self.recipient,
            sender_profile=self.sender,
            campaign=self.campaign,
            content="Лично и недокладвано",
        )
        pending = Valenitines.objects.create(
            profile=self.recipient,
            sender_profile=self.sender,
            campaign=self.campaign,
            content="Чака преглед",
            is_reported=True,
            report_reason="other",
            reported_at=timezone.now(),
            moderation_status="pending",
        )
        hidden = Valenitines.objects.create(
            profile=self.recipient,
            sender_profile=self.sender,
            campaign=self.campaign,
            content="Скрито",
            is_reported=True,
            report_reason="spam",
            reported_at=timezone.now(),
            moderation_status="hidden",
        )
        restored = Valenitines.objects.create(
            profile=self.recipient,
            sender_profile=self.sender,
            campaign=self.campaign,
            content="Възстановено",
            report_reason="other",
            reported_at=timezone.now(),
            moderation_status="restored",
        )
        request = RequestFactory().get("/admin/valentine/valenitines/")
        request.user = get_user_model().objects.create_superuser(
            username="valentine-admin",
            email="admin@example.com",
            password="test-password",
        )
        model_admin = ValentinesAdmin(Valenitines, AdminSite())

        visible_ids = set(
            model_admin.get_queryset(request).values_list("pk", flat=True)
        )

        self.assertNotIn(clear.pk, visible_ids)
        self.assertSetEqual(
            visible_ids,
            {pending.pk, hidden.pk, restored.pk},
        )
        self.assertFalse(model_admin.has_delete_permission(request))

    def test_restore_keeps_report_audit_information(self):
        reported_at = timezone.now()
        reported = Valenitines.objects.create(
            profile=self.recipient,
            sender_profile=self.sender,
            campaign=self.campaign,
            content="Докладвано",
            is_reported=True,
            report_reason="harassment",
            reported_at=reported_at,
            moderation_status="pending",
        )
        request = RequestFactory().post("/admin/")
        request.user = get_user_model().objects.create_superuser(
            username="moderator",
            email="moderator@example.com",
            password="test-password",
        )
        model_admin = ValentinesAdmin(Valenitines, AdminSite())

        with patch.object(model_admin, "message_user"):
            model_admin.restore_messages(
                request,
                Valenitines.objects.filter(pk=reported.pk),
            )

        reported.refresh_from_db()
        self.assertFalse(reported.is_reported)
        self.assertEqual(reported.moderation_status, "restored")
        self.assertEqual(reported.report_reason, "harassment")
        self.assertEqual(reported.reported_at, reported_at)

    def test_archived_campaign_is_separate_and_still_private(self):
        archived_campaign = ValentineCampaign.objects.create(
            year=self.campaign.year - 1,
            is_archived=True,
            archived_at=timezone.now(),
        )
        archived_pending = Valenitines.objects.create(
            profile=self.recipient,
            sender_profile=self.sender,
            campaign=archived_campaign,
            content="Стар докладван запис",
            is_reported=True,
            report_reason="other",
            reported_at=timezone.now(),
            moderation_status=Valenitines.ModerationStatus.PENDING,
        )
        archived_clear = Valenitines.objects.create(
            profile=self.recipient,
            sender_profile=self.sender,
            campaign=archived_campaign,
            content="Стар личен запис",
        )
        current_pending = Valenitines.objects.create(
            profile=self.recipient,
            sender_profile=self.sender,
            campaign=self.campaign,
            content="Текущ докладван запис",
            is_reported=True,
            report_reason="other",
            reported_at=timezone.now(),
            moderation_status=Valenitines.ModerationStatus.PENDING,
        )
        request = RequestFactory().get("/admin/")
        request.user = get_user_model().objects.create_superuser(
            username="archive-admin",
            email="archive@example.com",
            password="test-password",
        )
        current_admin = ValentinesAdmin(Valenitines, AdminSite())
        archive_admin = ArchivedValentinesAdmin(
            ArchivedValentine,
            AdminSite(),
        )

        current_ids = set(
            current_admin.get_queryset(request).values_list("pk", flat=True)
        )
        archive_ids = set(
            archive_admin.get_queryset(request).values_list("pk", flat=True)
        )

        self.assertIn(current_pending.pk, current_ids)
        self.assertNotIn(archived_pending.pk, current_ids)
        self.assertIn(archived_pending.pk, archive_ids)
        self.assertNotIn(archived_clear.pk, archive_ids)
        self.assertNotIn(current_pending.pk, archive_ids)

        archived_campaign.is_archived = False
        archived_campaign.archived_at = None
        archived_campaign.save(
            update_fields=("is_archived", "archived_at")
        )
        restored_campaign_ids = set(
            archive_admin.get_queryset(request).values_list("pk", flat=True)
        )
        self.assertIn(archived_pending.pk, restored_campaign_ids)

    def test_activating_new_campaign_archives_previous_one(self):
        next_campaign = ValentineCampaign.objects.create(
            year=self.campaign.year + 1,
        )
        request = RequestFactory().post(
            "/admin/valentine/valentinecampaign/",
            {
                "apply": "yes",
                "sending_starts_at": (
                    f"{next_campaign.year}-01-15T08:00"
                ),
                "sending_ends_at": (
                    f"{next_campaign.year}-02-13T23:59"
                ),
                "reveal_at": f"{next_campaign.year}-02-14T08:00",
            },
        )
        request.user = get_user_model().objects.create_superuser(
            username="campaign-admin",
            email="campaign@example.com",
            password="test-password",
        )
        model_admin = ValentineCampaignAdmin(
            ValentineCampaign,
            AdminSite(),
        )

        with patch.object(model_admin, "message_user"):
            model_admin.make_active(
                request,
                ValentineCampaign.objects.filter(pk=next_campaign.pk),
            )

        self.platform_settings.refresh_from_db()
        self.campaign.refresh_from_db()
        next_campaign.refresh_from_db()
        self.assertEqual(
            self.platform_settings.active_campaign,
            next_campaign,
        )
        self.assertTrue(self.campaign.is_archived)
        self.assertIsNotNone(self.campaign.archived_at)
        self.assertFalse(next_campaign.is_archived)

    def test_campaign_dashboard_is_aggregated_and_private(self):
        group_dispatch_id = uuid.uuid4()
        private_group_text = "Поверителен групов текст за статистиката"
        private_individual_text = "Поверителен индивидуален текст"
        Valenitines.objects.create(
            profile=self.recipient,
            sender_profile=self.sender,
            campaign=self.campaign,
            dispatch_id=group_dispatch_id,
            audience_type=Valenitines.AudienceType.CLASS,
            audience_label="10Б клас",
            content=private_group_text,
            is_reported=True,
            report_reason=Valenitines.ReportReason.OTHER,
            reported_at=timezone.now(),
            moderation_status=Valenitines.ModerationStatus.PENDING,
        )
        Valenitines.objects.create(
            profile=self.same_name_recipient,
            sender_profile=self.sender,
            campaign=self.campaign,
            dispatch_id=group_dispatch_id,
            audience_type=Valenitines.AudienceType.CLASS,
            audience_label="10Б клас",
            content=private_group_text,
        )
        Valenitines.objects.create(
            profile=self.sender,
            sender_profile=self.recipient,
            campaign=self.campaign,
            audience_type=Valenitines.AudienceType.INDIVIDUAL,
            audience_label=self.sender.full_name,
            content=private_individual_text,
            is_anonymous=False,
        )
        dashboard_admin = get_user_model().objects.create_superuser(
            username="statistics-admin",
            email="statistics@example.com",
            password="test-password",
        )
        self.client.force_login(dashboard_admin)

        dashboard_url = reverse(
            "admin:valentine_valentinecampaign_change",
            args=(self.campaign.pk,),
        )
        response = self.client.get(dashboard_url)

        self.assertEqual(response.status_code, 200)
        statistics = response.context["campaign_stats"]
        self.assertEqual(statistics["totals"]["dispatches"], 2)
        self.assertEqual(statistics["totals"]["deliveries"], 3)
        self.assertEqual(statistics["totals"]["unique_senders"], 2)
        self.assertEqual(statistics["totals"]["unique_recipients"], 3)
        self.assertEqual(statistics["totals"]["class_dispatches"], 1)
        self.assertEqual(statistics["totals"]["class_deliveries"], 2)
        self.assertEqual(statistics["totals"]["reports"], 1)
        self.assertEqual(statistics["totals"]["pending"], 1)
        self.assertContains(response, "Статистика за")
        self.assertContains(response, "Статистика по класове")
        self.assertContains(response, "valentines/admin_campaign.css")
        self.assertNotContains(response, private_group_text)
        self.assertNotContains(response, private_individual_text)
        self.assertNotContains(response, self.sender.full_name)
        self.assertNotContains(response, self.recipient.full_name)

        export_url = reverse(
            "admin:valentine_valentinecampaign_statistics",
            args=(self.campaign.pk,),
        )
        export_response = self.client.get(export_url)
        self.assertEqual(export_response.status_code, 200)
        self.assertEqual(
            export_response["Content-Type"],
            "text/csv; charset=utf-8",
        )
        exported = export_response.content.decode("utf-8-sig")
        self.assertIn("Обобщение", exported)
        self.assertIn("10А клас", exported)
        self.assertIn("10Б клас", exported)
        self.assertNotIn(private_group_text, exported)
        self.assertNotIn(private_individual_text, exported)
        self.assertNotIn(self.sender.full_name, exported)
        self.assertNotIn(self.recipient.full_name, exported)

    def test_users_only_see_dispatches_from_active_campaign(self):
        archived_campaign = ValentineCampaign.objects.create(
            year=self.campaign.year - 1,
            is_archived=True,
            archived_at=timezone.now(),
        )
        old_dispatch = Valenitines.objects.create(
            profile=self.recipient,
            sender_profile=self.sender,
            campaign=archived_campaign,
            content="Послание от миналата година",
        )
        Valenitines.objects.create(
            profile=self.recipient,
            sender_profile=self.sender,
            campaign=self.campaign,
            content="Послание от текущата година",
        )
        self.login()

        response = self.client.get(reverse("valentine:outbox"))

        self.assertContains(response, "Послание от текущата година")
        self.assertNotContains(response, "Послание от миналата година")
        edit_response = self.client.get(
            reverse("valentine:edit", args=(old_dispatch.dispatch_id,))
        )
        self.assertEqual(edit_response.status_code, 404)

    def test_revealed_card_has_png_download_control(self):
        self.platform_settings.sending_ends_at = timezone.now() - timedelta(
            minutes=2
        )
        self.platform_settings.reveal_at = timezone.now() - timedelta(minutes=1)
        self.platform_settings.save()
        Valenitines.objects.create(
            profile=self.recipient,
            sender_profile=self.sender,
            campaign=self.campaign,
            content="Картичка за снимка",
            theme=Valenitines.Theme.MIDNIGHT,
        )
        self.login(code="RECV01")

        response = self.client.get(reverse("valentine:inbox"))

        self.assertContains(response, "Картичка за снимка")
        self.assertContains(response, "data-download-card")
        self.assertContains(response, "data-download-image")
        self.assertContains(response, "Изтегли като снимка")

    def test_group_report_and_admin_moderation_apply_to_entire_dispatch(self):
        dispatch_id = uuid.uuid4()
        first = Valenitines.objects.create(
            profile=self.recipient,
            sender_profile=self.sender,
            campaign=self.campaign,
            dispatch_id=dispatch_id,
            audience_type=Valenitines.AudienceType.CLASS,
            audience_label="10Б клас",
            content="Една групова пратка",
        )
        second = Valenitines.objects.create(
            profile=self.same_name_recipient,
            sender_profile=self.sender,
            campaign=self.campaign,
            dispatch_id=dispatch_id,
            audience_type=Valenitines.AudienceType.CLASS,
            audience_label="10Б клас",
            content="Една групова пратка",
        )
        self.reveal_campaign()
        self.login(code=self.recipient.code)

        response = self.client.post(
            reverse("valentine:report", args=(first.pk,)),
            {"reason": Valenitines.ReportReason.HARASSMENT},
        )

        self.assertRedirects(response, reverse("valentine:inbox"))
        deliveries = Valenitines.objects.filter(dispatch_id=dispatch_id)
        self.assertEqual(
            deliveries.filter(
                moderation_status=Valenitines.ModerationStatus.PENDING,
                is_reported=True,
                report_reason=Valenitines.ReportReason.HARASSMENT,
            ).count(),
            2,
        )
        self.assertEqual(
            ValentineModerationEvent.objects.filter(
                dispatch_id=dispatch_id,
                event_type=(
                    ValentineModerationEvent.EventType.REPORTED
                ),
                reporter_profile=self.recipient,
            ).count(),
            1,
        )

        moderator = get_user_model().objects.create_superuser(
            username="dispatch-moderator",
            email="dispatch-moderator@example.com",
            password="test-password",
        )
        request = RequestFactory().post("/admin/")
        request.user = moderator
        model_admin = ValentinesAdmin(Valenitines, AdminSite())
        with patch.object(model_admin, "message_user"):
            model_admin.hide_reported(
                request,
                Valenitines.objects.filter(pk=first.pk),
            )
        self.assertEqual(
            deliveries.filter(
                moderation_status=Valenitines.ModerationStatus.HIDDEN,
                is_reported=True,
            ).count(),
            2,
        )

        with patch.object(model_admin, "message_user"):
            model_admin.restore_messages(
                request,
                Valenitines.objects.filter(pk=second.pk),
            )
        self.assertEqual(
            deliveries.filter(
                moderation_status=Valenitines.ModerationStatus.RESTORED,
                is_reported=False,
            ).count(),
            2,
        )
        self.assertEqual(
            ValentineModerationEvent.objects.filter(
                dispatch_id=dispatch_id,
                moderator=moderator,
            ).count(),
            2,
        )

        self.client.post(reverse("valentine:logout"))
        self.login(code=self.same_name_recipient.code)
        repeat = self.client.post(
            reverse("valentine:report", args=(second.pk,)),
            {"reason": Valenitines.ReportReason.SPAM},
        )
        self.assertRedirects(repeat, reverse("valentine:inbox"))
        self.assertEqual(
            deliveries.filter(
                moderation_status=Valenitines.ModerationStatus.RESTORED,
                is_reported=False,
            ).count(),
            2,
        )
        first.refresh_from_db()
        self.assertEqual(
            first.report_reason,
            Valenitines.ReportReason.HARASSMENT,
        )

    def test_outbox_only_offers_edit_for_unmoderated_dispatches(self):
        editable = Valenitines.objects.create(
            profile=self.recipient,
            sender_profile=self.sender,
            campaign=self.campaign,
            content="Може да се редактира",
        )
        moderated = Valenitines.objects.create(
            profile=self.same_name_recipient,
            sender_profile=self.sender,
            campaign=self.campaign,
            content="Не може да се редактира",
            is_reported=True,
            report_reason=Valenitines.ReportReason.OTHER,
            reported_at=timezone.now(),
            moderation_status=Valenitines.ModerationStatus.PENDING,
        )
        self.login()

        response = self.client.get(reverse("valentine:outbox"))

        self.assertContains(
            response,
            reverse("valentine:edit", args=(editable.dispatch_id,)),
        )
        self.assertNotContains(
            response,
            reverse("valentine:edit", args=(moderated.dispatch_id,)),
        )

    def test_home_does_not_embed_the_full_recipient_directory(self):
        extra = Profile.objects.create(
            code="PRIV01",
            first_name="Скрито",
            last_name="ИмеВДиректорията",
            clas="10B",
            email="private-directory@example.com",
        )
        dispatch_id = uuid.uuid4()
        Valenitines.objects.create(
            profile=self.recipient,
            sender_profile=self.sender,
            campaign=self.campaign,
            dispatch_id=dispatch_id,
            audience_type=Valenitines.AudienceType.CLASS,
            content="Групова",
        )
        Valenitines.objects.create(
            profile=extra,
            sender_profile=self.sender,
            campaign=self.campaign,
            dispatch_id=dispatch_id,
            audience_type=Valenitines.AudienceType.CLASS,
            content="Групова",
        )
        self.login()

        response = self.client.get(reverse("valentine:home"))

        self.assertNotContains(response, extra.full_name)
        self.assertNotContains(response, self.recipient.full_name)
        self.assertEqual(response.context["total_sent"], 1)
        self.assertContains(response, "пратки в училището")

    def test_profile_class_is_validated_and_normalized_by_the_model(self):
        cyrillic = Profile.objects.create(
            code="VALID1",
            first_name="Валиден",
            last_name="Клас",
            clas="8Б",
        )
        self.assertEqual(cyrillic.clas, "8B")

        with self.assertRaises(ValidationError):
            Profile.objects.create(
                code="BAD001",
                first_name="Невалиден",
                last_name="Клас",
                clas="13A",
            )

    def test_csv_never_updates_a_profile_by_name_only(self):
        row = {
            "row_number": 2,
            "first_name": self.recipient.first_name,
            "last_name": self.recipient.last_name,
            "clas": "9A",
            "email": "",
            "code": "",
        }

        plans, errors = build_profile_import_plan([row])

        self.assertFalse(errors)
        self.assertEqual(plans[0]["action"], "create")
        apply_profile_import([row], 2026)
        self.recipient.refresh_from_db()
        self.assertEqual(self.recipient.clas, "10B")
        self.assertEqual(
            Profile.objects.filter(
                first_name=self.recipient.first_name,
                last_name=self.recipient.last_name,
            ).count(),
            3,
        )

    def test_csv_requires_exact_code_when_email_is_not_unique(self):
        Profile.objects.create(
            code="DUPL01",
            first_name="Друг",
            last_name="Профил",
            clas="9A",
            email=self.recipient.email,
        )
        row = {
            "row_number": 2,
            "first_name": "Ново",
            "last_name": "Име",
            "clas": "9B",
            "email": self.recipient.email,
            "code": "",
        }

        _plans, errors = build_profile_import_plan([row])

        self.assertTrue(errors)
        self.assertIn("няколко профила", errors[0])
        with self.assertRaises(ProfileImportError):
            apply_profile_import([row], 2026)

    def test_csv_add_permission_cannot_update_or_reveal_existing_code(self):
        user = get_user_model().objects.create_user(
            username="limited-importer",
            password="test-password",
            is_staff=True,
        )
        user.user_permissions.add(
            Permission.objects.get(
                codename="add_profile",
                content_type__app_label="valentine",
            ),
            Permission.objects.get(
                codename="view_profile",
                content_type__app_label="valentine",
            ),
        )
        self.client.force_login(user)
        upload = SimpleUploadedFile(
            "students.csv",
            (
                "first_name,last_name,class,email,code\n"
                "Променено,Име,8A,,SEND01\n"
            ).encode("utf-8"),
            content_type="text/csv",
        )

        response = self.client.post(
            reverse("admin:valentine_profile_import_csv"),
            {
                "academic_year_start": "2026",
                "csv_file": upload,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Нямаш право да обновяваш или възстановяваш",
        )
        self.assertNotContains(response, "SEND01")
        self.sender.refresh_from_db()
        self.assertEqual(self.sender.first_name, "Иван")

        changelist = self.client.get(
            reverse("admin:valentine_profile_changelist")
        )
        self.assertNotContains(changelist, "Всички кодове в PDF")
        pdf = self.client.get(
            reverse("admin:valentine_profile_active_codes_pdf")
        )
        self.assertEqual(pdf.status_code, 403)

    def test_profile_code_export_requires_dedicated_permission(self):
        user = get_user_model().objects.create_user(
            username="code-exporter",
            password="test-password",
            is_staff=True,
        )
        user.user_permissions.add(
            Permission.objects.get(
                codename="view_profile",
                content_type__app_label="valentine",
            ),
            Permission.objects.get(
                codename="view_profile_codes",
                content_type__app_label="valentine",
            ),
        )
        self.client.force_login(user)

        changelist = self.client.get(
            reverse("admin:valentine_profile_changelist")
        )
        self.assertContains(changelist, "Всички кодове в PDF")
        pdf = self.client.get(
            reverse("admin:valentine_profile_active_codes_pdf")
        )
        self.assertEqual(pdf.status_code, 200)

    def test_class_promotion_cannot_repeat_for_same_target_year(self):
        source_year = self.recipient.academic_year_start
        target_year = source_year + 1
        admin_user = get_user_model().objects.create_superuser(
            username="repeat-promotion-admin",
            email="repeat-promotion@example.com",
            password="test-password",
        )
        request = RequestFactory().post(
            "/admin/valentine/profile/",
            {
                "apply": "yes",
                "target_academic_year_start": str(target_year),
            },
        )
        request.user = admin_user
        model_admin = ProfileAdmin(Profile, AdminSite())
        queryset = Profile.objects.filter(pk=self.recipient.pk)

        with patch.object(model_admin, "message_user"):
            model_admin.promote_to_next_academic_year(request, queryset)
            model_admin.promote_to_next_academic_year(request, queryset)

        self.recipient.refresh_from_db()
        self.assertEqual(self.recipient.clas, "11B")
        self.assertEqual(
            self.recipient.academic_year_start,
            target_year,
        )

    def test_campaign_activation_rejects_dates_from_another_year(self):
        next_campaign = ValentineCampaign.objects.create(
            year=self.campaign.year + 1,
        )
        request = RequestFactory().post(
            "/admin/valentine/valentinecampaign/",
            {
                "apply": "yes",
                "sending_starts_at": (
                    f"{next_campaign.year - 1}-01-15T08:00"
                ),
                "sending_ends_at": (
                    f"{next_campaign.year - 1}-02-13T23:59"
                ),
                "reveal_at": (
                    f"{next_campaign.year - 1}-02-14T08:00"
                ),
            },
        )
        request.user = get_user_model().objects.create_superuser(
            username="invalid-campaign-admin",
            email="invalid-campaign@example.com",
            password="test-password",
        )
        model_admin = ValentineCampaignAdmin(
            ValentineCampaign,
            AdminSite(),
        )

        response = model_admin.make_active(
            request,
            ValentineCampaign.objects.filter(pk=next_campaign.pk),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context_data["activation_form"].errors)
        self.platform_settings.refresh_from_db()
        self.assertEqual(
            self.platform_settings.active_campaign,
            self.campaign,
        )

    def test_settings_reveal_year_must_match_active_campaign(self):
        wrong_year = self.campaign.year + 1
        self.platform_settings.reveal_at = timezone.make_aware(
            datetime(wrong_year, 2, 14, 8, 0),
            timezone.get_current_timezone(),
        )
        self.platform_settings.reveal_date = date(wrong_year, 2, 14)

        with self.assertRaisesMessage(
            ValidationError,
            "Годината на разкриване трябва да съвпада",
        ):
            self.platform_settings.save()

    @override_settings(TIME_ZONE="Europe/Sofia")
    def test_campaign_year_uses_local_reveal_time_not_utc_year(self):
        target_year = self.campaign.year + 2
        self.platform_settings.active_campaign = None
        self.platform_settings.sending_starts_at = None
        self.platform_settings.sending_ends_at = None
        self.platform_settings.reveal_at = datetime(
            target_year - 1,
            12,
            31,
            22,
            30,
            tzinfo=datetime_timezone.utc,
        )
        self.platform_settings.reveal_date = date(target_year, 1, 1)
        self.platform_settings.save()

        campaign = self.platform_settings.ensure_active_campaign()

        self.assertEqual(campaign.year, target_year)

    def test_settings_singleton_and_admin_add_permission(self):
        with self.assertRaises(ValidationError):
            Settings.objects.create(
                reveal_date=date.today() + timedelta(days=10),
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            Settings.objects.bulk_create([
                Settings(
                    singleton_key="other",
                    reveal_date=date.today() + timedelta(days=10),
                )
            ])

        user = get_user_model().objects.create_user(
            username="settings-viewer",
            password="test-password",
            is_staff=True,
        )
        request = RequestFactory().get("/admin/valentine/settings/")
        request.user = user
        from .admin import SettingsAdmin

        model_admin = SettingsAdmin(Settings, AdminSite())
        with patch(
            "valentine.admin.Settings.objects.exists",
            return_value=False,
        ):
            self.assertFalse(model_admin.has_add_permission(request))

        user.user_permissions.add(
            Permission.objects.get(
                codename="add_settings",
                content_type__app_label="valentine",
            )
        )
        user = get_user_model().objects.get(pk=user.pk)
        request.user = user
        with patch(
            "valentine.admin.Settings.objects.exists",
            return_value=False,
        ):
            self.assertTrue(model_admin.has_add_permission(request))

    @override_settings(
        VALENTINE_TRUSTED_PROXY_IPS=("10.0.0.10", "10.0.0.11")
    )
    def test_client_ip_only_trusts_forwarding_from_configured_proxies(self):
        factory = RequestFactory()
        untrusted = factory.get(
            "/valentines/",
            REMOTE_ADDR="203.0.113.20",
            HTTP_X_FORWARDED_FOR="198.51.100.40",
        )
        trusted_chain = factory.get(
            "/valentines/",
            REMOTE_ADDR="10.0.0.10",
            HTTP_X_FORWARDED_FOR=(
                "192.0.2.99, 198.51.100.40, 10.0.0.11"
            ),
        )

        self.assertEqual(_client_ip(untrusted), "203.0.113.20")
        self.assertEqual(_client_ip(trusted_chain), "198.51.100.40")

    def test_login_throttle_blocks_after_the_configured_failures(self):
        for _index in range(5):
            response = self.client.post(
                reverse("valentine:login"),
                {"code": "WRONG1"},
                REMOTE_ADDR="192.0.2.10",
            )
            self.assertContains(response, "Кодът е невалиден")

        blocked = self.client.post(
            reverse("valentine:login"),
            {"code": self.sender.code},
            REMOTE_ADDR="192.0.2.10",
        )

        self.assertEqual(blocked.status_code, 200)
        self.assertContains(blocked, "твърде много опити")
        self.assertNotIn("authv", blocked.cookies)

    def test_successful_login_does_not_reset_ip_failure_counter(self):
        remote_addr = "192.0.2.11"
        for _index in range(4):
            self.client.post(
                reverse("valentine:login"),
                {"code": "WRONG1"},
                REMOTE_ADDR=remote_addr,
            )

        successful = self.client.post(
            reverse("valentine:login"),
            {"code": self.sender.code},
            REMOTE_ADDR=remote_addr,
        )
        self.assertRedirects(successful, reverse("valentine:home"))
        self.client.post(
            reverse("valentine:logout"),
            REMOTE_ADDR=remote_addr,
        )
        fifth_failure = self.client.post(
            reverse("valentine:login"),
            {"code": "WRONG1"},
            REMOTE_ADDR=remote_addr,
        )
        self.assertContains(fifth_failure, "Кодът е невалиден")

        blocked = self.client.post(
            reverse("valentine:login"),
            {"code": "WRONG1"},
            REMOTE_ADDR=remote_addr,
        )
        self.assertContains(blocked, "твърде много опити")

    def test_login_failure_window_resets_after_ttl(self):
        remote_addr = "192.0.2.12"
        for _index in range(5):
            self.client.post(
                reverse("valentine:login"),
                {"code": "WRONG1"},
                REMOTE_ADDR=remote_addr,
            )
        counter = ValentineLoginAttempt.objects.get()
        counter.window_expires = timezone.now() - timedelta(seconds=1)
        counter.save(update_fields=("window_expires",))

        response = self.client.post(
            reverse("valentine:login"),
            {"code": "WRONG1"},
            REMOTE_ADDR=remote_addr,
        )

        self.assertContains(response, "Кодът е невалиден")
        counter.refresh_from_db()
        self.assertEqual(counter.attempts, 1)
        self.assertGreater(counter.window_expires, timezone.now())

    def test_login_counter_retries_a_simultaneous_first_create_race(self):
        original_create = ValentineLoginAttempt.objects.create
        calls = 0

        def race_once(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise IntegrityError("simulated unique-key race")
            return original_create(**kwargs)

        with patch.object(
            ValentineLoginAttempt.objects,
            "create",
            side_effect=race_once,
        ):
            status, session = _authenticate_login(
                "WRONG1",
                "first-create-race-key",
            )

        self.assertEqual(status, LOGIN_STATUS_INVALID)
        self.assertIsNone(session)
        self.assertEqual(calls, 2)
        counter = ValentineLoginAttempt.objects.get(
            client_key="first-create-race-key"
        )
        self.assertEqual(counter.attempts, 1)

    def test_expired_session_delete_action_requires_delete_permission(self):
        user = get_user_model().objects.create_user(
            username="session-manager",
            password="test-password",
            is_staff=True,
        )
        user.user_permissions.add(
            Permission.objects.get(
                codename="view_register",
                content_type__app_label="valentine",
            ),
            Permission.objects.get(
                codename="change_register",
                content_type__app_label="valentine",
            ),
        )
        request = RequestFactory().get("/admin/valentine/register/")
        request.user = user
        model_admin = RegisterAdmin(Register, AdminSite())

        self.assertNotIn(
            "delete_expired_sessions",
            model_admin.get_actions(request),
        )

    def test_profile_codes_are_not_admin_search_fields_without_permission(self):
        user = get_user_model().objects.create_user(
            username="search-without-codes",
            password="test-password",
            is_staff=True,
        )
        request = RequestFactory().get("/admin/")
        request.user = user
        register_admin = RegisterAdmin(Register, AdminSite())
        valentine_admin = ValentinesAdmin(Valenitines, AdminSite())

        self.assertNotIn(
            "profile__code",
            register_admin.get_search_fields(request),
        )
        self.assertNotIn(
            "profile__code",
            valentine_admin.get_search_fields(request),
        )

        user.user_permissions.add(
            Permission.objects.get(
                codename="view_profile_codes",
                content_type__app_label="valentine",
            )
        )
        request.user = get_user_model().objects.get(pk=user.pk)
        self.assertIn(
            "profile__code",
            register_admin.get_search_fields(request),
        )
        self.assertIn(
            "profile__code",
            valentine_admin.get_search_fields(request),
        )

    def test_campaign_year_is_immutable_after_creation(self):
        request = RequestFactory().get("/admin/")
        request.user = get_user_model().objects.create_superuser(
            username="immutable-campaign-year",
            email="immutable-year@example.com",
            password="test-password",
        )
        model_admin = ValentineCampaignAdmin(
            ValentineCampaign,
            AdminSite(),
        )

        self.assertNotIn(
            "year",
            model_admin.get_readonly_fields(request),
        )
        self.assertIn(
            "year",
            model_admin.get_readonly_fields(request, self.campaign),
        )
