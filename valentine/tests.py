import uuid
from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .admin import (
    ArchivedValentinesAdmin,
    ValentineCampaignAdmin,
    ValentinesAdmin,
)
from .models import (
    ArchivedValentine,
    Profile,
    Settings,
    ValentineCampaign,
    Valenitines,
)


@override_settings(DEBUG=False)
class ValentinePlatformTests(TestCase):
    def setUp(self):
        cache.clear()
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

    def test_activating_new_campaign_archives_previous_one(self):
        next_campaign = ValentineCampaign.objects.create(
            year=self.campaign.year + 1,
        )
        request = RequestFactory().post(
            "/admin/valentine/valentinecampaign/"
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
