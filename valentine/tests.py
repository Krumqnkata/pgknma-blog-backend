from datetime import date, timedelta

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Profile, Settings, Valenitines


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
        self.platform_settings = Settings.objects.create(
            reveal_date=date.today() + timedelta(days=3),
            reveal_at=timezone.now() + timedelta(days=3),
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
            content="Тайно послание",
        )
        self.login()

        response = self.client.get(reverse("valentine:inbox"))

        self.assertContains(response, "Все още са запечатани")
        self.assertNotContains(response, "Тайно послание")

    def test_inbox_reveals_messages_after_reveal_time(self):
        self.platform_settings.reveal_at = timezone.now() - timedelta(minutes=1)
        self.platform_settings.save()
        Valenitines.objects.create(
            profile=self.sender,
            sender_profile=self.recipient,
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
            content="Неподходящо",
        )
        foreign = Valenitines.objects.create(
            profile=self.same_name_recipient,
            sender_profile=self.recipient,
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
