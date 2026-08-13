import io
import json
from datetime import timedelta
from unittest import mock

from django.contrib.auth.models import User
from django.contrib.sessions.models import Session as DjangoSession
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from valentine.models import (
    Profile,
    Register,
    Valenitines,
    ValentineCampaign,
    ValentineLoginAttempt,
    ValentineModerationEvent,
)

from .models import (
    BlogLoginSession,
    ConsentRecord,
    Notification,
    PersonalDataCleanupRun,
)


@override_settings(PERSONAL_DATA_RETENTION_DAYS=90)
class PersonalDataRetentionTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.old_time = self.now - timedelta(days=91)
        self.user = User.objects.create_user(
            username='retention-user',
            password='A-strong-test-password-2026',
        )
        self.sender = Profile.objects.create(
            first_name='Sender',
            last_name='Test',
            clas='11A',
        )
        self.recipient = Profile.objects.create(
            first_name='Recipient',
            last_name='Test',
            clas='11B',
        )

    def make_consent(self, *, old):
        record = ConsentRecord.objects.create(
            user=self.user,
            ip_address='203.0.113.10',
            network_operator='Test Network',
            network_asn=64500,
            consent_status='REJECTED',
            policy_version='test-v1',
        )
        if old:
            ConsentRecord.objects.filter(pk=record.pk).update(
                timestamp=self.old_time
            )
        return record

    def make_blog_session(self, *, old):
        session = BlogLoginSession.objects.create(
            user=self.user,
            auth_method=BlogLoginSession.AUTH_PASSWORD,
            ip_address='203.0.113.11',
            network_operator='Test Network',
            network_asn=64500,
            device_label='Firefox · Linux',
            last_seen_at=self.now,
            expires_at=self.now + timedelta(days=7),
        )
        if old:
            BlogLoginSession.objects.filter(pk=session.pk).update(
                created_at=self.old_time
            )
        return session

    def make_valentine_session(self, *, old):
        session = Register.objects.create(
            profile=self.sender,
            ip_address='203.0.113.12',
            network_operator='Test Network',
            network_asn=64500,
        )
        if old:
            Register.objects.filter(pk=session.pk).update(
                created_at=self.old_time
            )
        return session

    def make_login_attempt(self, client_key, *, old):
        attempt = ValentineLoginAttempt.objects.create(
            client_key=client_key,
            attempts=3,
            window_expires=self.now + timedelta(minutes=15),
        )
        if old:
            ValentineLoginAttempt.objects.filter(pk=attempt.pk).update(
                updated_at=self.old_time
            )
        return attempt

    def test_command_removes_old_personal_metadata_but_keeps_content(self):
        old_consent = self.make_consent(old=True)
        fresh_consent = self.make_consent(old=False)
        old_blog_session = self.make_blog_session(old=True)
        fresh_blog_session = self.make_blog_session(old=False)
        old_valentine_session = self.make_valentine_session(old=True)
        fresh_valentine_session = self.make_valentine_session(old=False)
        old_attempt = self.make_login_attempt('old-client-key', old=True)
        fresh_attempt = self.make_login_attempt('new-client-key', old=False)

        expired_django_session = DjangoSession.objects.create(
            session_key='expired-session-key',
            session_data='',
            expire_date=self.now - timedelta(minutes=1),
        )
        fresh_django_session = DjangoSession.objects.create(
            session_key='fresh-session-key',
            session_data='',
            expire_date=self.now + timedelta(days=1),
        )

        expired_refresh = RefreshToken.for_user(self.user)
        fresh_refresh = RefreshToken.for_user(self.user)
        expired_outstanding = OutstandingToken.objects.get(
            jti=str(expired_refresh['jti'])
        )
        fresh_outstanding = OutstandingToken.objects.get(
            jti=str(fresh_refresh['jti'])
        )
        OutstandingToken.objects.filter(pk=expired_outstanding.pk).update(
            expires_at=self.now - timedelta(minutes=1)
        )

        campaign = ValentineCampaign.objects.create(year=2026)
        valentine = Valenitines.objects.create(
            profile=self.recipient,
            sender_profile=self.sender,
            campaign=campaign,
            content='Archived content must remain.',
        )
        Valenitines.objects.filter(pk=valentine.pk).update(date=self.old_time)
        moderation_event = ValentineModerationEvent.objects.create(
            campaign=campaign,
            dispatch_id=valentine.dispatch_id,
            event_type=ValentineModerationEvent.EventType.REPORTED,
            reporter_profile=self.recipient,
            reason=Valenitines.ReportReason.OTHER,
        )
        ValentineModerationEvent.objects.filter(pk=moderation_event.pk).update(
            created_at=self.old_time
        )

        output = io.StringIO()
        call_command('cleanup_personal_data', days=90, stdout=output)
        result = json.loads(output.getvalue())

        self.assertEqual(result['retention_days'], 90)
        self.assertEqual(result['status'], PersonalDataCleanupRun.STATUS_SUCCEEDED)
        self.assertFalse(ConsentRecord.objects.filter(pk=old_consent.pk).exists())
        self.assertTrue(ConsentRecord.objects.filter(pk=fresh_consent.pk).exists())
        self.assertFalse(BlogLoginSession.objects.filter(pk=old_blog_session.pk).exists())
        self.assertTrue(BlogLoginSession.objects.filter(pk=fresh_blog_session.pk).exists())
        self.assertFalse(Register.objects.filter(pk=old_valentine_session.pk).exists())
        self.assertTrue(Register.objects.filter(pk=fresh_valentine_session.pk).exists())
        self.assertFalse(ValentineLoginAttempt.objects.filter(pk=old_attempt.pk).exists())
        self.assertTrue(ValentineLoginAttempt.objects.filter(pk=fresh_attempt.pk).exists())
        self.assertFalse(DjangoSession.objects.filter(pk=expired_django_session.pk).exists())
        self.assertTrue(DjangoSession.objects.filter(pk=fresh_django_session.pk).exists())
        self.assertFalse(OutstandingToken.objects.filter(pk=expired_outstanding.pk).exists())
        self.assertTrue(OutstandingToken.objects.filter(pk=fresh_outstanding.pk).exists())

        # Valentine messages, campaigns, profiles and moderation evidence are
        # content records with separate retention purposes and are untouched.
        self.assertTrue(Valenitines.objects.filter(pk=valentine.pk).exists())
        self.assertTrue(
            ValentineModerationEvent.objects.filter(pk=moderation_event.pk).exists()
        )
        self.assertTrue(Profile.objects.filter(pk=self.sender.pk).exists())
        self.assertTrue(ValentineCampaign.objects.filter(pk=campaign.pk).exists())

        cleanup_run = PersonalDataCleanupRun.objects.get(
            pk=result['cleanup_run_id']
        )
        self.assertEqual(
            cleanup_run.status,
            PersonalDataCleanupRun.STATUS_SUCCEEDED,
        )
        self.assertFalse(cleanup_run.dry_run)
        self.assertEqual(cleanup_run.retention_days, 90)
        self.assertEqual(cleanup_run.result_counts['consent_records'], 1)
        self.assertIsNotNone(cleanup_run.finished_at)
        self.assertEqual(cleanup_run.error_message, '')

    def test_dry_run_does_not_change_data(self):
        old_consent = self.make_consent(old=True)
        output = io.StringIO()

        call_command(
            'cleanup_personal_data',
            days=90,
            dry_run=True,
            stdout=output,
        )

        result = json.loads(output.getvalue())
        self.assertTrue(result['dry_run'])
        self.assertEqual(result['consent_records'], 1)
        self.assertTrue(
            ConsentRecord.objects.filter(pk=old_consent.pk).exists()
        )
        cleanup_run = PersonalDataCleanupRun.objects.get(
            pk=result['cleanup_run_id']
        )
        self.assertTrue(cleanup_run.dry_run)
        self.assertEqual(
            cleanup_run.status,
            PersonalDataCleanupRun.STATUS_SUCCEEDED,
        )
        self.assertEqual(cleanup_run.affected_records, 1)

    def test_expired_notifications_remain_archived_for_the_retention_window(self):
        old_expired = Notification.objects.create(
            user=self.user,
            text='Старо автоматично архивирано известие',
            expires_at=self.old_time,
        )
        recently_expired = Notification.objects.create(
            user=self.user,
            text='Скорошно автоматично архивирано известие',
            expires_at=self.now - timedelta(days=1),
        )
        important = Notification.objects.create(
            user=self.user,
            text='Важно известие без срок',
            important=True,
            expires_at=self.old_time,
        )

        output = io.StringIO()
        call_command('cleanup_personal_data', days=90, stdout=output)
        result = json.loads(output.getvalue())

        self.assertEqual(result['expired_notifications'], 1)
        self.assertFalse(Notification.objects.filter(pk=old_expired.pk).exists())
        self.assertTrue(Notification.objects.filter(pk=recently_expired.pk).exists())
        self.assertTrue(Notification.objects.filter(pk=important.pk).exists())
        important.refresh_from_db()
        self.assertIsNone(important.expires_at)

    def test_command_rejects_invalid_retention_period(self):
        with self.assertRaises(CommandError):
            call_command('cleanup_personal_data', days=0)
        self.assertFalse(PersonalDataCleanupRun.objects.exists())

    @mock.patch(
        'blog.management.commands.cleanup_personal_data.cleanup_personal_data',
        side_effect=RuntimeError('Test cleanup failure'),
    )
    def test_command_records_failure(self, _cleanup_mock):
        with self.assertRaisesMessage(RuntimeError, 'Test cleanup failure'):
            call_command('cleanup_personal_data', days=90)

        cleanup_run = PersonalDataCleanupRun.objects.get()
        self.assertEqual(
            cleanup_run.status,
            PersonalDataCleanupRun.STATUS_FAILED,
        )
        self.assertIsNotNone(cleanup_run.finished_at)
        self.assertEqual(cleanup_run.result_counts, {})
        self.assertEqual(cleanup_run.error_message, 'Test cleanup failure')
