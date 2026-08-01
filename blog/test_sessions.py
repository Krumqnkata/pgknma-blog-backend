from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory, APITestCase
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from .models import BlogLoginSession
from .session_auth import SESSION_ID_CLAIM, issue_session_tokens


@override_settings(
    TRUSTED_PROXY_IPS=('127.0.0.1',),
    BLOG_SESSION_RETENTION_DAYS=90,
)
class BlogLoginSessionTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='session-user',
            email='session@example.com',
            password='A-strong-test-password-2026',
        )
        self.factory = APIRequestFactory()

    def issue(self, user=None, auth_method='password', ip='203.0.113.20'):
        request = self.factory.post(
            '/api/auth/token/',
            HTTP_X_FORWARDED_FOR=ip,
            HTTP_USER_AGENT=(
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 Chrome/126.0 Safari/537.36'
            ),
        )
        with patch(
            'blog.session_auth.lookup_network_operator',
            return_value=('Test Network', 64500),
        ):
            return issue_session_tokens(
                user or self.user,
                request,
                auth_method,
            )

    def authenticate(self, refresh):
        self.client.credentials(
            HTTP_AUTHORIZATION=f'Bearer {refresh.access_token}'
        )

    def test_issued_tokens_are_bound_to_persisted_session(self):
        login_session, refresh = self.issue()
        access = refresh.access_token

        self.assertEqual(refresh[SESSION_ID_CLAIM], str(login_session.pk))
        self.assertEqual(access[SESSION_ID_CLAIM], str(login_session.pk))
        self.assertEqual(login_session.ip_address, '203.0.113.20')
        self.assertEqual(login_session.network_operator, 'Test Network')
        self.assertEqual(login_session.network_asn, 64500)
        self.assertEqual(login_session.device_label, 'Chrome · Windows')

    def test_session_list_only_returns_current_users_sessions(self):
        own_session, own_refresh = self.issue()
        other_user = User.objects.create_user('other-session-user')
        self.issue(user=other_user)
        self.authenticate(own_refresh)

        response = self.client.get('/api/profile/sessions/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['id'], str(own_session.pk))
        self.assertTrue(response.data[0]['is_current'])

    def test_revoked_session_rejects_existing_access_token_immediately(self):
        login_session, refresh = self.issue()
        access = str(refresh.access_token)
        login_session.revoked_at = timezone.now()
        login_session.revoked_reason = BlogLoginSession.REVOKED_USER
        login_session.save(update_fields=('revoked_at', 'revoked_reason'))
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access}')

        response = self.client.get('/api/profile/sessions/')

        self.assertEqual(response.status_code, 401)

    def test_refresh_keeps_session_id_and_rotates_stored_jti(self):
        login_session, refresh = self.issue()
        old_jti = login_session.refresh_jti

        response = self.client.post(
            '/api/auth/token/refresh/',
            {'refresh': str(refresh)},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        login_session.refresh_from_db()
        rotated_refresh = RefreshToken(response.data['refresh'])
        rotated_access = AccessToken(response.data['access'])
        self.assertNotEqual(login_session.refresh_jti, old_jti)
        self.assertEqual(
            rotated_refresh[SESSION_ID_CLAIM],
            str(login_session.pk),
        )
        self.assertEqual(
            rotated_access[SESSION_ID_CLAIM],
            str(login_session.pk),
        )

    def test_revoke_others_preserves_current_session(self):
        current_session, current_refresh = self.issue()
        other_session, _ = self.issue(ip='203.0.113.21')
        self.authenticate(current_refresh)

        response = self.client.post(
            '/api/profile/sessions/revoke-others/',
            {},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        current_session.refresh_from_db()
        other_session.refresh_from_db()
        self.assertIsNone(current_session.revoked_at)
        self.assertIsNotNone(other_session.revoked_at)
        self.assertEqual(response.data['revoked_count'], 1)

    def test_user_cannot_revoke_another_users_session(self):
        _, current_refresh = self.issue()
        other_user = User.objects.create_user('foreign-session-user')
        foreign_session, _ = self.issue(user=other_user)
        self.authenticate(current_refresh)

        response = self.client.post(
            f'/api/profile/sessions/{foreign_session.pk}/revoke/',
            {},
            format='json',
        )

        self.assertEqual(response.status_code, 404)
        foreign_session.refresh_from_db()
        self.assertIsNone(foreign_session.revoked_at)

    def test_logout_revokes_current_session(self):
        login_session, refresh = self.issue()
        access = str(refresh.access_token)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access}')

        response = self.client.post(
            '/api/auth/logout/',
            {'refresh': str(refresh)},
            format='json',
        )

        self.assertEqual(response.status_code, 205)
        login_session.refresh_from_db()
        self.assertEqual(
            login_session.revoked_reason,
            BlogLoginSession.REVOKED_LOGOUT,
        )
        self.assertIsNotNone(login_session.revoked_at)
        denied = self.client.get('/api/profile/sessions/')
        self.assertEqual(denied.status_code, 401)

    def test_password_change_revokes_all_sessions(self):
        current_session, refresh = self.issue()
        other_session, _ = self.issue(ip='203.0.113.23')
        self.authenticate(refresh)

        response = self.client.put(
            '/api/auth/profile/change-password/',
            {
                'current_password': 'A-strong-test-password-2026',
                'new_password': 'Another-strong-password-2026',
                'new_password_confirm': 'Another-strong-password-2026',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        current_session.refresh_from_db()
        other_session.refresh_from_db()
        self.assertEqual(
            current_session.revoked_reason,
            BlogLoginSession.REVOKED_PASSWORD_CHANGE,
        )
        self.assertEqual(
            other_session.revoked_reason,
            BlogLoginSession.REVOKED_PASSWORD_CHANGE,
        )

    def test_cleanup_removes_only_sessions_older_than_retention_period(self):
        old_session, _ = self.issue()
        recent_session, _ = self.issue(ip='203.0.113.22')
        old_time = timezone.now() - timedelta(days=100)
        BlogLoginSession.objects.filter(pk=old_session.pk).update(
            created_at=old_time,
            revoked_at=old_time,
            revoked_reason=BlogLoginSession.REVOKED_USER,
        )

        call_command('cleanup_blog_sessions', days=90, verbosity=0)

        self.assertFalse(
            BlogLoginSession.objects.filter(pk=old_session.pk).exists()
        )
        self.assertTrue(
            BlogLoginSession.objects.filter(pk=recent_session.pk).exists()
        )
