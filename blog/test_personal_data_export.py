import json
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from .models import (
    BlogLoginSession,
    Category,
    Comments,
    ConsentRecord,
    ContactSubmission,
    Posts,
)


@override_settings(
    REST_FRAMEWORK={
        'DEFAULT_AUTHENTICATION_CLASSES': (
            'blog.session_auth.SessionJWTAuthentication',
        ),
        'DEFAULT_PERMISSION_CLASSES': (
            'rest_framework.permissions.IsAuthenticated',
        ),
        'DEFAULT_THROTTLE_CLASSES': (),
    }
)
class PersonalDataExportTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='export-user',
            email='export@example.com',
            password='A-strong-export-password-2026',
            first_name='Експорт',
            last_name='Потребител',
        )
        self.other_user = User.objects.create_user(
            username='other-user',
            email='other@example.com',
            password='A-strong-other-password-2026',
        )
        category = Category.objects.create(
            full_name='Тестова категория',
            short_name='test-export',
        )
        self.post = Posts.objects.create(
            title='Тестова публикация',
            category=category,
            hook='Кратко описание',
            content='Съдържание на публикацията',
            author=self.user,
        )
        self.own_comment = Comments.objects.create(
            user=self.user,
            post=self.post,
            content='Моят коментар',
        )
        self.other_comment = Comments.objects.create(
            user=self.other_user,
            post=self.post,
            content='Чужд коментар',
        )
        ConsentRecord.objects.create(
            user=self.user,
            ip_address='203.0.113.20',
            network_operator='Example Network',
            network_asn=64500,
            consent_status='REJECTED',
            policy_version='privacy-v1',
        )
        ContactSubmission.objects.create(
            user=self.user,
            name='Експорт Потребител',
            email='export@example.com',
            reason='technical_issue',
            message='Тестово съобщение',
        )
        self.session = BlogLoginSession.objects.create(
            user=self.user,
            auth_method=BlogLoginSession.AUTH_PASSWORD,
            ip_address='203.0.113.21',
            network_operator='Example Network',
            network_asn=64500,
            device_label='Firefox · Linux',
            last_seen_at=timezone.now(),
            expires_at=timezone.now() + timedelta(days=7),
            refresh_jti='secret-refresh-identifier',
        )
        BlogLoginSession.objects.create(
            user=self.other_user,
            auth_method=BlogLoginSession.AUTH_PASSWORD,
            ip_address='198.51.100.30',
            device_label='Other device',
            last_seen_at=timezone.now(),
            expires_at=timezone.now() + timedelta(days=7),
            refresh_jti='other-secret-refresh-identifier',
        )

    def test_authenticated_user_downloads_only_own_data(self):
        self.client.force_authenticate(self.user)

        response = self.client.get('/api/profile/export/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response['Content-Type'],
            'application/json; charset=utf-8',
        )
        self.assertIn('attachment;', response['Content-Disposition'])
        self.assertIn('no-store', response['Cache-Control'])
        payload = json.loads(response.content.decode('utf-8'))
        self.assertEqual(payload['scope'], 'pgknma-blog')
        self.assertEqual(payload['account']['username'], self.user.username)
        self.assertEqual(
            [item['id'] for item in payload['created_content']['comments']],
            [self.own_comment.pk],
        )
        self.assertEqual(
            payload['created_content']['contact_submissions'][0]['message'],
            'Тестово съобщение',
        )
        self.assertEqual(
            payload['privacy_and_security']['login_sessions'][0]['id'],
            str(self.session.pk),
        )
        body = response.content.decode('utf-8')
        self.assertNotIn(self.user.password, body)
        self.assertNotIn('secret-refresh-identifier', body)
        self.assertNotIn('Чужд коментар', body)
        self.assertNotIn('198.51.100.30', body)

    def test_export_requires_authentication(self):
        response = self.client.get('/api/profile/export/')

        self.assertEqual(response.status_code, 401)
