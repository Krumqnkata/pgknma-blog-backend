from datetime import timedelta
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from .email_verification import send_verification_email
from .models import BlogLoginSession, EmailVerificationToken, SiteSettings, UserProfile


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    PUBLIC_SITE_URL='https://pgknma.space',
    FRONTEND_URL='https://pgknma.space',
    TURNSTILE_SECRET_KEY='test-turnstile-secret',
    TURNSTILE_VERIFY_URL='https://turnstile.test/verify',
    EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS=60,
)
class EmailVerificationTests(TestCase):
    registration_payload = {
        'username': 'NewVerifiedStudent',
        'email': 'student@example.com',
        'first_name': 'New',
        'last_name': 'Student',
        'password': 'A-strong-test-password-2026',
        'password2': 'A-strong-test-password-2026',
        'captcha_answer': 'captcha-token',
    }

    def _turnstile_success(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {'success': True}
        return response

    @patch('blog.serializer.requests.post')
    def test_password_registration_sends_one_time_email(self, post):
        post.return_value = self._turnstile_success()

        response = self.client.post(
            '/api/auth/register/',
            self.registration_payload,
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.json()['verification_required'])
        self.assertTrue(response.json()['email_sent'])
        user = User.objects.get(username='NewVerifiedStudent')
        profile = UserProfile.objects.get(user=user)
        self.assertFalse(profile.email_verified)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Потвърдете имейла си', mail.outbox[0].subject)
        self.assertIn('/api/auth/verify-email/?token=', mail.outbox[0].body)
        html_body = mail.outbox[0].alternatives[0].content
        self.assertIn(
            'src="https://pgknma.space/static/images/logo-sm.png"',
            html_body,
        )

        token = EmailVerificationToken.objects.get(user=user)
        verification_url = next(
            part for part in mail.outbox[0].body.split()
            if '/api/auth/verify-email/?token=' in part
        )
        raw_token = parse_qs(urlparse(verification_url).query)['token'][0]
        self.assertNotEqual(token.token_hash, raw_token)
        self.assertEqual(len(token.token_hash), 64)

    @patch('blog.serializer.requests.post')
    def test_registration_reports_daily_resend_quota(self, post):
        post.return_value = self._turnstile_success()
        resend_response = Mock()
        resend_response.json.return_value = {
            'name': 'daily_quota_exceeded',
            'message': 'You have reached your daily email quota.',
        }
        resend_response.text = 'daily_quota_exceeded'
        error = RuntimeError('Resend returned HTTP 429')
        error.status_code = 429
        error.response = resend_response

        with patch(
            'blog.email_verification.EmailMultiAlternatives.send',
            side_effect=error,
        ):
            response = self.client.post(
                '/api/auth/register/',
                self.registration_payload,
                content_type='application/json',
            )

        self.assertEqual(response.status_code, 201)
        self.assertFalse(response.json()['email_sent'])
        self.assertEqual(
            response.json()['email_failure_reason'],
            'daily_quota_exceeded',
        )
        self.assertIn('дневният лимит', response.json()['detail'])

    def test_valid_token_verifies_and_redirects_without_jwt(self):
        user = User.objects.create_user(
            username='pending',
            email='pending@example.com',
            password='A-strong-test-password-2026',
        )
        profile = UserProfile.objects.get(user=user)
        profile.email_verified = False
        profile.save(update_fields=('email_verified',))
        send_verification_email(user)
        verification_url = next(
            part for part in mail.outbox[0].body.split()
            if '/api/auth/verify-email/?token=' in part
        )
        parsed = urlparse(verification_url)

        response = self.client.get(f'{parsed.path}?{parsed.query}')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response['Location'],
            'https://pgknma.space/verify-email?status=verified',
        )
        self.assertNotIn('access=', response['Location'])
        self.assertNotIn('refresh=', response['Location'])
        profile.refresh_from_db()
        self.assertTrue(profile.email_verified)
        self.assertIsNotNone(profile.email_verified_at)

        second = self.client.get(f'{parsed.path}?{parsed.query}')
        self.assertEqual(
            second['Location'],
            'https://pgknma.space/verify-email?status=already-verified',
        )

    @patch('blog.serializer.requests.post')
    def test_unverified_password_account_cannot_create_session(self, post):
        post.return_value = self._turnstile_success()
        user = User.objects.create_user(
            username='not-verified',
            email='not-verified@example.com',
            password='A-strong-test-password-2026',
        )
        profile = UserProfile.objects.get(user=user)
        profile.email_verified = False
        profile.save(update_fields=('email_verified',))

        response = self.client.post(
            '/api/auth/token/',
            {
                'username': user.username,
                'password': 'A-strong-test-password-2026',
                'captcha_answer': 'captcha-token',
            },
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['code'], ['email_not_verified'])
        self.assertFalse(BlogLoginSession.objects.filter(user=user).exists())

    def test_resend_is_generic_and_respects_cooldown(self):
        user = User.objects.create_user(
            username='resend-user',
            email='resend@example.com',
        )
        profile = UserProfile.objects.get(user=user)
        profile.email_verified = False
        profile.save(update_fields=('email_verified',))

        first = self.client.post(
            '/api/auth/verify-email/resend/',
            {'email': user.email},
            content_type='application/json',
        )
        second = self.client.post(
            '/api/auth/verify-email/resend/',
            {'email': user.email},
            content_type='application/json',
        )
        missing = self.client.post(
            '/api/auth/verify-email/resend/',
            {'email': 'missing@example.com'},
            content_type='application/json',
        )

        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(missing.status_code, 202)
        self.assertEqual(first.json(), missing.json())
        self.assertEqual(len(mail.outbox), 1)

    @patch(
        'blog.email_verification.EmailMultiAlternatives.send',
        side_effect=RuntimeError('Resend unavailable'),
    )
    def test_delivery_failure_can_fail_open_only_when_admin_enabled(self, _send):
        user = User.objects.create_user(
            username='fail-open',
            email='fail-open@example.com',
        )
        profile = UserProfile.objects.get(user=user)
        profile.email_verified = False
        profile.save(update_fields=('email_verified',))

        closed_result = send_verification_email(user)
        profile.refresh_from_db()
        self.assertFalse(closed_result.auto_verified)
        self.assertFalse(profile.email_verified)

        site_settings, _ = SiteSettings.objects.get_or_create(pk=1)
        site_settings.auto_verify_on_email_failure = True
        site_settings.save(update_fields=('auto_verify_on_email_failure',))
        open_result = send_verification_email(user)
        profile.refresh_from_db()
        self.assertTrue(open_result.auto_verified)
        self.assertTrue(profile.email_verified)

    def test_expired_token_redirects_to_expired_status(self):
        user = User.objects.create_user(
            username='expired-user',
            email='expired@example.com',
        )
        profile = UserProfile.objects.get(user=user)
        profile.email_verified = False
        profile.save(update_fields=('email_verified',))
        send_verification_email(user)
        token = EmailVerificationToken.objects.get(user=user)
        token.expires_at = timezone.now() - timedelta(seconds=1)
        token.save(update_fields=('expires_at',))
        verification_url = next(
            part for part in mail.outbox[0].body.split()
            if '/api/auth/verify-email/?token=' in part
        )
        parsed = urlparse(verification_url)

        response = self.client.get(f'{parsed.path}?{parsed.query}')

        self.assertEqual(
            response['Location'],
            'https://pgknma.space/verify-email?status=expired',
        )
