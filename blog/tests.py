import base64
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient, APIRequestFactory
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView

from .models import (
    BellSongSuggestion,
    Category,
    Comments,
    ConsentRecord,
    Cookie,
    Event,
    GoogleIdentity,
    MemeOfWeek,
    Notification,
    PollAnswer,
    PollOption,
    PollQuestion,
    PostDocument,
    PostImage,
    Posts,
    PrivacyPolicy,
    SiteSettings,
    TermsOfService,
    UserProfile,
    meme_upload_path,
    validate_document_upload_size,
    validate_image_upload_size,
)
from .serializer import (
    BellSongSuggestionSerializer,
    CommentSerializer,
    ContactSubmissionSerializer,
    MemeOfWeekSerializer,
    MyTokenObtainPairView,
    NotificationSerializer,
    PasswordChangeSerializer,
    PollAnswerSerializer,
    RegisterSerializer,
)
from .network import get_client_ip
from .throttling import (
    TrustedProxyAnonRateThrottle,
    TrustedProxyScopedRateThrottle,
    TrustedProxyUserRateThrottle,
)


def make_user(username='student', email=None):
    return User.objects.create_user(
        username=username,
        email=email or f'{username}@example.com',
        password='A-strong-test-password-2026',
    )


def public_post(author, suffix='one', **overrides):
    category = Category.objects.create(
        full_name=f'Category {suffix}',
        short_name=f'category-{suffix}',
    )
    values = {
        'title': f'Post {suffix}',
        'category': category,
        'hook': 'Hook',
        'content': 'Content',
        'author': author,
        'published': True,
        'allowed': True,
    }
    values.update(overrides)
    return Posts.objects.create(**values)


@override_settings(PUBLIC_SITE_URL='https://pgknma.space')
class PostSharePreviewTests(TestCase):
    def setUp(self):
        self.user = make_user('preview-author')
        self.frontend_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.frontend_directory.cleanup)
        self.frontend_index = Path(self.frontend_directory.name) / 'index.html'
        self.frontend_index.write_text(
            '''<!doctype html><html lang="bg"><head>
<title>Default title</title>
<meta name="description" content="Default description">
<link rel="canonical" href="https://pgknma.space/">
<meta property="og:title" content="Default title">
<meta property="og:image" content="https://pgknma.space/default.jpg">
<meta name="twitter:card" content="summary_large_image">
</head><body><div id="root"></div><script src="/assets/app.js"></script></body></html>''',
            encoding='utf-8',
        )
        self.post = public_post(
            self.user,
            'preview',
            title='Новина <тест>',
            hook='Описание & още',
            banner='posts/preview/banner.jpg',
        )

    def test_clean_post_url_serves_spa_with_post_banner_metadata(self):
        with override_settings(FRONTEND_INDEX_PATH=self.frontend_index):
            response = self.client.get(reverse('post-page', args=[self.post.pk]))
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<div id="root"></div>')
        self.assertContains(response, '<script src="/assets/app.js"></script>')
        self.assertContains(response, 'property="og:type" content="article"')
        self.assertContains(
            response,
            'property="og:image" content="https://pgknma.space/files/posts/preview/banner.jpg"',
        )
        self.assertContains(response, 'Новина &lt;тест&gt;')
        self.assertContains(response, 'Описание &amp; още')
        self.assertNotIn('Default title', content)
        self.assertNotIn('default.jpg', content)
        self.assertNotIn('http-equiv="refresh"', content)

    def test_clean_post_url_does_not_expose_draft_posts(self):
        draft = public_post(
            self.user,
            'page-draft',
            published=False,
            allowed=False,
        )

        with override_settings(FRONTEND_INDEX_PATH=self.frontend_index):
            response = self.client.get(reverse('post-page', args=[draft.pk]))

        self.assertEqual(response.status_code, 404)

    def test_clean_post_url_fails_gracefully_without_frontend_build(self):
        missing_index = Path(self.frontend_directory.name) / 'missing.html'

        with override_settings(FRONTEND_INDEX_PATH=missing_index):
            response = self.client.get(reverse('post-page', args=[self.post.pk]))

        self.assertEqual(response.status_code, 503)

    def test_preview_contains_safe_social_metadata_and_redirect(self):
        response = self.client.get(reverse('posts-share-preview', args=[self.post.pk]))
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/html; charset=utf-8')
        self.assertContains(response, 'property="og:type" content="article"')
        self.assertContains(response, 'name="twitter:card" content="summary_large_image"')
        self.assertContains(response, 'Новина &lt;тест&gt;')
        self.assertContains(response, 'Описание &amp; още')
        self.assertIn(f'https://pgknma.space/post/{self.post.pk}', content)

    def test_preview_does_not_expose_draft_posts(self):
        draft = public_post(
            self.user,
            'preview-draft',
            published=False,
            allowed=False,
        )

        response = self.client.get(reverse('posts-share-preview', args=[draft.pk]))

        self.assertEqual(response.status_code, 404)


class ContentVisibilityTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = make_user()
        self.public = public_post(self.user, 'public')
        self.draft = public_post(
            self.user,
            'draft',
            published=False,
            allowed=False,
        )

    def test_list_apis_return_arrays_for_frontend_client_side_pagination(self):
        """The React client fetches all items and paginates locally."""
        BellSongSuggestion.objects.create(
            user=self.user,
            title="Approved song",
            link="https://example.com/song",
            status="approved",
        )

        posts_response = self.client.get(reverse("posts-list"))
        songs_response = self.client.get(reverse("approved-songs-list"))

        self.assertIsInstance(posts_response.json(), list)
        self.assertIsInstance(songs_response.json(), list)
        self.assertEqual(posts_response.json()[0]["id"], self.public.pk)
        self.assertEqual(songs_response.json()[0]["title"], "Approved song")

    def test_unapproved_meme_cannot_be_retrieved_publicly(self):
        meme = MemeOfWeek.objects.create(
            user=self.user,
            title='Pending',
            image='memes/pending.png',
            status='pending',
        )

        response = self.client.get(f'/api/memes/{meme.pk}/')

        self.assertEqual(response.status_code, 404)

    def test_approved_meme_is_public(self):
        meme = MemeOfWeek.objects.create(
            user=self.user,
            title='Approved',
            image='memes/approved.png',
            status='approved',
        )

        response = self.client.get(f'/api/memes/{meme.pk}/')

        self.assertEqual(response.status_code, 200)

    def test_comments_for_draft_are_not_public(self):
        Comments.objects.create(
            user=self.user,
            post=self.draft,
            content='Hidden comment',
        )

        response = self.client.get(
            f'/api/posts/{self.draft.pk}/comments/'
        )

        self.assertEqual(response.status_code, 404)

    def test_comment_cannot_be_added_to_draft(self):
        self.client.force_authenticate(self.user)

        response = self.client.post(
            f'/api/posts/{self.draft.pk}/comments/add/',
            {'content': 'Nope'},
            format='json',
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(Comments.objects.filter(content='Nope').exists())

    def test_parent_comment_must_belong_to_same_post(self):
        other_post = public_post(self.user, 'other')
        other_user = make_user('other-commenter')
        foreign_parent = Comments.objects.create(
            user=other_user,
            post=other_post,
            content='Other thread',
        )
        self.client.force_authenticate(self.user)

        response = self.client.post(
            f'/api/posts/{self.public.pk}/comments/add/',
            {'content': 'Cross-post reply', 'parent': foreign_parent.pk},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('parent', response.data)

    def test_comment_cooldown_rejects_immediate_second_comment(self):
        self.client.force_authenticate(self.user)

        first = self.client.post(
            f'/api/posts/{self.public.pk}/comments/add/',
            {'content': 'First'},
            format='json',
        )
        second = self.client.post(
            f'/api/posts/{self.public.pk}/comments/add/',
            {'content': 'Second'},
            format='json',
        )

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 429)
        self.assertFalse(Comments.objects.filter(content='Second').exists())


class ProtectedMemeImageTests(TestCase):
    PNG_BYTES = base64.b64decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwC'
        'AAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='
    )

    def setUp(self):
        self.media_directory = tempfile.TemporaryDirectory()
        self.media_override = override_settings(
            MEDIA_ROOT=self.media_directory.name,
        )
        self.media_override.enable()
        self.addCleanup(self._cleanup_media)

        self.client = APIClient()
        self.owner = make_user('meme-owner')
        self.staff = make_user('meme-staff')
        self.staff.is_staff = True
        self.staff.save(update_fields=['is_staff'])
        self.site_settings = SiteSettings.objects.create(
            enable_meme_of_the_week=True,
        )
        self.pending = self._create_meme(
            title='Pending protected meme',
            filename='pending.png',
            approved=False,
        )
        self.approved = self._create_meme(
            title='Approved protected meme',
            filename='approved.png',
            approved=True,
        )

    def _cleanup_media(self):
        self.media_override.disable()
        self.media_directory.cleanup()

    def _create_meme(self, *, title, filename, approved):
        return MemeOfWeek.objects.create(
            user=self.owner,
            title=title,
            image=SimpleUploadedFile(
                filename,
                self.PNG_BYTES,
                content_type='image/png',
            ),
            status='approved' if approved else 'pending',
        )

    def _image_url(self, meme):
        return reverse('meme-image', kwargs={'pk': meme.pk})

    def _consume_image_response(self, response):
        content = b''.join(response.streaming_content)
        response.close()
        return content

    def test_pending_image_is_hidden_from_anonymous_users(self):
        response = self.client.get(self._image_url(self.pending))

        self.assertEqual(response.status_code, 404)

    def test_owner_can_read_pending_image_with_secure_headers(self):
        self.client.force_authenticate(self.owner)

        response = self.client.get(self._image_url(self.pending))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        self.assertIn('Authorization', response['Vary'])
        self.assertIn('Cookie', response['Vary'])
        self.assertEqual(response['Content-Type'], 'image/png')
        self.assertEqual(self._consume_image_response(response), self.PNG_BYTES)

    def test_approved_image_is_public_when_feature_is_enabled(self):
        response = self.client.get(self._image_url(self.approved))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._consume_image_response(response), self.PNG_BYTES)

    def test_direct_media_url_is_always_blocked(self):
        for meme in (self.pending, self.approved):
            with self.subTest(meme=meme.pk):
                self.assertTrue(meme.image.url.startswith('/files/memes/'))
                response = self.client.get(meme.image.url)
                self.assertEqual(response.status_code, 404)

    def test_disabled_feature_hides_approved_image_but_not_from_owner(self):
        self.site_settings.enable_meme_of_the_week = False
        self.site_settings.save(update_fields=['enable_meme_of_the_week'])

        anonymous_response = self.client.get(self._image_url(self.approved))
        self.client.force_authenticate(self.owner)
        owner_response = self.client.get(self._image_url(self.pending))

        self.assertEqual(anonymous_response.status_code, 404)
        self.assertEqual(owner_response.status_code, 200)
        self._consume_image_response(owner_response)

    def test_maintenance_blocks_public_and_owner_but_allows_staff_session(self):
        self.site_settings.maintenance_mode = True
        self.site_settings.save(update_fields=['maintenance_mode'])

        anonymous_response = self.client.get(self._image_url(self.approved))
        self.client.force_authenticate(self.owner)
        owner_response = self.client.get(self._image_url(self.pending))
        staff_client = APIClient()
        staff_client.force_login(self.staff)
        staff_response = staff_client.get(self._image_url(self.pending))

        self.assertEqual(anonymous_response.status_code, 503)
        self.assertEqual(owner_response.status_code, 503)
        self.assertEqual(staff_response.status_code, 200)
        self._consume_image_response(staff_response)

    def test_missing_file_returns_404(self):
        self.pending.image.storage.delete(self.pending.image.name)
        self.client.force_authenticate(self.owner)

        response = self.client.get(self._image_url(self.pending))

        self.assertEqual(response.status_code, 404)

    def test_serializer_and_admin_previews_use_protected_url(self):
        from django.contrib import admin

        from .admin import MemeOfWeekAdmin
        from .forms import MemeSelectionForm

        protected_url = self._image_url(self.pending)
        serializer_data = MemeOfWeekSerializer(self.pending).data
        admin_preview = str(
            MemeOfWeekAdmin(MemeOfWeek, admin.site).image_preview(self.pending)
        )
        selection_label = str(
            MemeSelectionForm().fields[f'meme_{self.pending.pk}'].label
        )

        self.assertEqual(serializer_data['image_url'], protected_url)
        self.assertIn(protected_url, admin_preview)
        self.assertIn(protected_url, selection_label)
        self.assertNotIn('/files/memes/', admin_preview)
        self.assertNotIn('/files/memes/', selection_label)


class PollIntegrityTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = make_user()
        now = timezone.now()
        self.question = PollQuestion.objects.create(
            title='Question one',
            start_date=now - timedelta(hours=1),
            end_date=now + timedelta(hours=1),
        )
        self.option = PollOption.objects.create(
            question=self.question,
            text='Correct',
            key='a',
            is_correct=True,
        )
        self.other_question = PollQuestion.objects.create(
            title='Question two',
            start_date=now - timedelta(hours=1),
            end_date=now + timedelta(hours=1),
        )
        self.other_option = PollOption.objects.create(
            question=self.other_question,
            text='Other',
            key='a',
        )

    def test_serializer_rejects_option_from_another_question(self):
        serializer = PollAnswerSerializer(data={
            'question': self.question.pk,
            'selected_option': self.other_option.pk,
        })

        self.assertFalse(serializer.is_valid())
        self.assertIn('selected_option', serializer.errors)

    def test_model_rejects_duplicate_answer(self):
        PollAnswer.objects.create(
            user=self.user,
            question=self.question,
            selected_option=self.option,
        )

        with self.assertRaises(ValidationError):
            PollAnswer.objects.create(
                user=self.user,
                question=self.question,
                selected_option=self.option,
            )

    def test_submit_is_idempotently_rejected_after_first_answer(self):
        self.client.force_authenticate(self.user)
        payload = {
            'question': self.question.pk,
            'selected_option': self.option.pk,
        }

        first = self.client.post('/api/poll/submit/', payload, format='json')
        second = self.client.post('/api/poll/submit/', payload, format='json')

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(
            PollAnswer.objects.filter(
                user=self.user,
                question=self.question,
            ).count(),
            1,
        )

    @patch('blog.views.timezone.now')
    def test_submit_rechecks_time_after_question_lock(self, mocked_now):
        mocked_now.return_value = self.question.end_date + timedelta(seconds=1)
        self.client.force_authenticate(self.user)

        response = self.client.post(
            '/api/poll/submit/',
            {
                'question': self.question.pk,
                'selected_option': self.option.pk,
            },
            format='json',
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(PollAnswer.objects.exists())

    def test_public_statistics_include_top_five_leaderboard(self):
        PollAnswer.objects.create(
            user=self.user,
            question=self.question,
            selected_option=self.option,
        )

        response = self.client.get('/api/poll/statistics/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(response.data),
            {'total_participants', 'total_answers', 'correct_answers', 'leaderboard'},
        )
        self.assertEqual(response.data['leaderboard'], [{
            'id': self.user.id,
            'username': self.user.username,
            'correct_answers': 1,
        }])

    def test_poll_and_event_validate_date_ranges(self):
        now = timezone.now()
        poll = PollQuestion(
            title='Invalid',
            start_date=now,
            end_date=now,
        )
        event = Event(
            title='Invalid event',
            start_datetime=now,
            end_datetime=now - timedelta(seconds=1),
            location='School',
            category='School',
            description='Description',
            attendees_text='All',
        )

        with self.assertRaises(ValidationError):
            poll.full_clean()
        with self.assertRaises(ValidationError):
            event.full_clean()

    def test_poll_default_end_is_after_default_start(self):
        poll = PollQuestion(title='Defaults are valid')

        self.assertGreater(poll.end_date, poll.start_date)


class VoteIntegrityTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = make_user()
        self.other_user = make_user('other')
        self.song = BellSongSuggestion.objects.create(
            user=self.user,
            title='Song',
            link='https://youtu.be/dQw4w9WgXcQ',
            status='approved',
        )
        self.meme = MemeOfWeek.objects.create(
            user=self.user,
            title='Meme',
            image='memes/meme.png',
            status='approved',
        )

    def test_duplicate_api_votes_do_not_increment_counter(self):
        self.client.force_authenticate(self.user)

        first = self.client.post(f'/api/songs/{self.song.pk}/vote/')
        second = self.client.post(f'/api/songs/{self.song.pk}/vote/')

        self.song.refresh_from_db()
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 400)
        self.assertEqual(self.song.votes, 1)
        self.assertEqual(self.song.voted_by.count(), 1)

    def test_m2m_changes_and_user_deletion_keep_counters_in_sync(self):
        self.meme.voted_by.add(self.user, self.other_user)
        self.meme.refresh_from_db()
        self.assertEqual(self.meme.votes, 2)

        self.other_user.delete()

        self.meme.refresh_from_db()
        self.assertEqual(self.meme.votes, 1)
        self.assertEqual(self.meme.voted_by.count(), 1)


@override_settings(
    GOOGLE_CLIENT_ID='test-client',
    TURNSTILE_SECRET_KEY='test-turnstile-secret',
    TURNSTILE_VERIFY_URL='https://turnstile.test/verify',
)
class AuthenticationHardeningTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()

    @staticmethod
    def google_claims(**overrides):
        claims = {
            'iss': 'https://accounts.google.com',
            'sub': 'google-subject-1',
            'email': 'Student@Example.com',
            'email_verified': True,
            'given_name': 'Student',
            'family_name': 'Test',
        }
        claims.update(overrides)
        return claims

    @patch('blog.auth.id_token.verify_oauth2_token')
    def test_google_requires_verified_email(self, verify):
        verify.return_value = self.google_claims(email_verified=False)

        response = self.client.post(
            '/api/auth/google/',
            {'credential': 'credential'},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(User.objects.exists())

    @override_settings(GOOGLE_CLIENT_ID='')
    def test_google_fails_closed_without_client_configuration(self):
        response = self.client.post(
            '/api/auth/google/',
            {'credential': 'credential'},
            format='json',
        )

        self.assertEqual(response.status_code, 503)
        self.assertFalse(User.objects.exists())

    @patch('blog.auth.id_token.verify_oauth2_token')
    def test_google_does_not_silently_claim_existing_email(self, verify):
        make_user(email='student@example.com')
        verify.return_value = self.google_claims()

        response = self.client.post(
            '/api/auth/google/',
            {'credential': 'credential'},
            format='json',
        )

        self.assertEqual(response.status_code, 409)
        self.assertFalse(GoogleIdentity.objects.exists())

    @patch('blog.auth.id_token.verify_oauth2_token')
    def test_legacy_google_only_account_is_migrated_once(self, verify):
        legacy = User.objects.create(
            username='legacy-google',
            email='Student@Example.com',
            is_active=True,
        )
        legacy.set_unusable_password()
        legacy.save(update_fields=['password'])
        SiteSettings.objects.create(enable_user_registration=False)
        verify.return_value = self.google_claims()

        response = self.client.post(
            '/api/auth/google/',
            {'credential': 'credential'},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        legacy.refresh_from_db()
        self.assertEqual(legacy.email, 'student@example.com')
        self.assertFalse(legacy.has_usable_password())
        self.assertEqual(legacy.google_identity.subject, 'google-subject-1')
        self.assertTrue(
            UserProfile.objects.filter(user=legacy).exists()
        )

    @patch('blog.auth.id_token.verify_oauth2_token')
    def test_duplicate_legacy_email_is_never_auto_linked(self, verify):
        User.objects.create(
            username='legacy-google',
            email='student@example.com',
            password='',
            is_active=True,
        )
        make_user('duplicate', email='Student@Example.com')
        verify.return_value = self.google_claims()

        response = self.client.post(
            '/api/auth/google/',
            {'credential': 'credential'},
            format='json',
        )

        self.assertEqual(response.status_code, 409)
        self.assertFalse(GoogleIdentity.objects.exists())

    @patch('blog.auth.id_token.verify_oauth2_token')
    def test_existing_account_can_explicitly_link_matching_google(self, verify):
        user = make_user(email='student@example.com')
        verify.return_value = self.google_claims()
        self.client.force_authenticate(user)

        linked = self.client.post(
            '/api/auth/google/link/',
            {
                'credential': 'credential',
                'current_password': 'A-strong-test-password-2026',
            },
            format='json',
        )
        self.client.force_authenticate(user=None)
        signed_in = self.client.post(
            '/api/auth/google/',
            {'credential': 'credential'},
            format='json',
        )

        self.assertEqual(linked.status_code, 200)
        self.assertEqual(signed_in.status_code, 200)
        self.assertEqual(user.google_identity.subject, 'google-subject-1')

    @patch('blog.auth.id_token.verify_oauth2_token')
    def test_google_link_requires_current_password(self, verify):
        user = make_user(email='student@example.com')
        verify.return_value = self.google_claims()
        self.client.force_authenticate(user)

        response = self.client.post(
            '/api/auth/google/link/',
            {'credential': 'credential', 'current_password': 'wrong'},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(GoogleIdentity.objects.exists())
        verify.assert_called_once()

    @patch('blog.auth.id_token.verify_oauth2_token')
    def test_google_link_email_mismatch_never_checks_password(self, verify):
        user = make_user(email='student@example.com')
        verify.return_value = self.google_claims(
            email='attacker@example.com',
        )
        self.client.force_authenticate(user)

        with patch.object(
            User,
            'check_password',
            side_effect=AssertionError('password oracle'),
        ) as check_password:
            response = self.client.post(
                '/api/auth/google/link/',
                {
                    'credential': 'credential',
                    'current_password': 'any-value',
                },
                format='json',
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data['error'],
            'Google акаунтът не съвпада с профила.',
        )
        check_password.assert_not_called()
        self.assertFalse(GoogleIdentity.objects.exists())

    @patch('blog.auth.id_token.verify_oauth2_token')
    def test_google_new_account_obeys_registration_flag(self, verify):
        SiteSettings.objects.create(enable_user_registration=False)
        verify.return_value = self.google_claims()

        response = self.client.post(
            '/api/auth/google/',
            {'credential': 'credential'},
            format='json',
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(User.objects.exists())

    @patch('blog.auth.id_token.verify_oauth2_token')
    def test_google_does_not_create_new_user_during_maintenance(self, verify):
        SiteSettings.objects.create(
            maintenance_mode=True,
            enable_user_registration=True,
        )
        verify.return_value = self.google_claims()

        response = self.client.post(
            '/api/auth/google/',
            {'credential': 'credential'},
            format='json',
        )

        self.assertEqual(response.status_code, 503)
        self.assertFalse(User.objects.exists())

    @patch('blog.auth.id_token.verify_oauth2_token')
    def test_google_binds_by_subject_and_normalizes_email(self, verify):
        verify.return_value = self.google_claims()

        first = self.client.post(
            '/api/auth/google/',
            {'credential': 'credential'},
            format='json',
        )
        SiteSettings.objects.update(enable_user_registration=False)
        second = self.client.post(
            '/api/auth/google/',
            {'credential': 'credential'},
            format='json',
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(User.objects.count(), 1)
        user = User.objects.get()
        self.assertEqual(user.email, 'student@example.com')
        self.assertFalse(user.has_usable_password())
        self.assertEqual(user.google_identity.subject, 'google-subject-1')

    @patch('blog.auth.id_token.verify_oauth2_token')
    def test_google_internal_errors_are_generic(self, verify):
        verify.side_effect = RuntimeError('database password must not leak')

        with patch('blog.auth.logger.exception'):
            response = self.client.post(
                '/api/auth/google/',
                {'credential': 'credential'},
                format='json',
            )

        self.assertEqual(response.status_code, 503)
        self.assertNotIn('database password', str(response.data))

    @patch('blog.serializer.requests.post')
    def test_custom_login_uses_configured_turnstile(self, post):
        user = make_user()
        verify_response = Mock()
        verify_response.raise_for_status.return_value = None
        verify_response.json.return_value = {'success': True}
        post.return_value = verify_response
        request = APIRequestFactory().post(
            '/api/auth/token/',
            {
                'username': user.username,
                'password': 'A-strong-test-password-2026',
                'captcha_answer': 'captcha',
            },
            format='json',
        )

        response = MyTokenObtainPairView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertIn('access', response.data)
        self.assertEqual(
            post.call_args.args[0],
            'https://turnstile.test/verify',
        )
        self.assertEqual(
            post.call_args.kwargs['data']['remoteip'],
            '127.0.0.1',
        )

    def test_logout_blacklists_refresh_token(self):
        user = make_user()
        refresh = RefreshToken.for_user(user)
        self.client.force_authenticate(user)

        logout = self.client.post(
            '/api/auth/logout/',
            {'refresh': str(refresh)},
            format='json',
        )
        refresh_request = APIRequestFactory().post(
            '/api/auth/token/refresh/',
            {'refresh': str(refresh)},
            format='json',
        )
        refreshed = TokenRefreshView.as_view()(refresh_request)

        self.assertEqual(logout.status_code, 205)
        self.assertEqual(refreshed.status_code, 401)

    def test_logout_cannot_blacklist_another_users_refresh_token(self):
        user = make_user()
        other_user = make_user('other')
        refresh = RefreshToken.for_user(other_user)
        self.client.force_authenticate(user)

        logout = self.client.post(
            '/api/auth/logout/',
            {'refresh': str(refresh)},
            format='json',
        )
        refresh_request = APIRequestFactory().post(
            '/api/auth/token/refresh/',
            {'refresh': str(refresh)},
            format='json',
        )
        refreshed = TokenRefreshView.as_view()(refresh_request)

        self.assertEqual(logout.status_code, 400)
        self.assertEqual(refreshed.status_code, 200)

    def test_username_change_integrity_race_returns_validation_error(self):
        user = make_user()
        self.client.force_authenticate(user)

        with patch.object(User, 'save', side_effect=IntegrityError):
            response = self.client.put(
                '/api/auth/profile/change-username/',
                {
                    'current_password': 'A-strong-test-password-2026',
                    'new_username': 'available-name',
                },
                format='json',
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn('new_username', response.data)

    def test_account_deactivate_options_is_safe(self):
        user = make_user()
        self.client.force_authenticate(user)

        response = self.client.options('/api/auth/profile/deactivate/')

        self.assertEqual(response.status_code, 200)
        self.assertIn('PUT', response.headers['Allow'])


@override_settings(
    TURNSTILE_SECRET_KEY='test-turnstile-secret',
    TURNSTILE_VERIFY_URL='https://turnstile.test/verify',
)
class RegistrationAndUploadValidationTests(TestCase):
    @patch('blog.serializer.requests.post')
    def test_registration_normalizes_data_and_rejects_case_duplicate(self, post):
        verify_response = Mock()
        verify_response.raise_for_status.return_value = None
        verify_response.json.return_value = {'success': True}
        post.return_value = verify_response
        request = APIRequestFactory().post('/api/auth/register/')
        payload = {
            'username': '  NewStudent  ',
            'email': ' NewStudent@Example.COM ',
            'first_name': '  New ',
            'last_name': ' Student  ',
            'password': 'A-strong-test-password-2026',
            'password2': 'A-strong-test-password-2026',
            'captcha_answer': 'captcha',
        }
        serializer = RegisterSerializer(
            data=payload,
            context={'request': request},
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        user = serializer.save()
        self.assertEqual(user.username, 'NewStudent')
        self.assertEqual(user.email, 'newstudent@example.com')
        self.assertEqual(user.first_name, 'New')
        duplicate = RegisterSerializer(
            data={**payload, 'username': 'Different'},
            context={'request': request},
        )
        self.assertFalse(duplicate.is_valid())
        self.assertIn('email', duplicate.errors)

    def test_registration_rejects_blank_identity_fields(self):
        serializer = RegisterSerializer(data={
            'username': ' ',
            'email': ' ',
            'first_name': ' ',
            'last_name': ' ',
            'password': 'A-strong-test-password-2026',
            'password2': 'A-strong-test-password-2026',
            'captcha_answer': 'captcha',
        })

        self.assertFalse(serializer.is_valid())
        for field in ('username', 'email', 'first_name', 'last_name'):
            self.assertIn(field, serializer.errors)

    def test_registration_password_is_checked_against_user_attributes(self):
        serializer = RegisterSerializer(data={
            'username': 'VerySimilarUsername',
            'email': 'student@example.com',
            'first_name': 'Student',
            'last_name': 'Test',
            'password': 'VerySimilarUsername',
            'password2': 'VerySimilarUsername',
            'captcha_answer': 'captcha',
        })

        self.assertFalse(serializer.is_valid())
        self.assertIn('password', serializer.errors)

    def test_password_change_uses_current_user_for_similarity_checks(self):
        user = make_user('VerySimilarUsername')
        request = APIRequestFactory().put('/api/auth/profile/change-password/')
        request.user = user
        serializer = PasswordChangeSerializer(
            data={
                'current_password': 'A-strong-test-password-2026',
                'new_password': 'VerySimilarUsername',
                'new_password_confirm': 'VerySimilarUsername',
            },
            context={'request': request},
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn('new_password', serializer.errors)

    @patch('blog.serializer.requests.post')
    def test_registration_rechecks_username_inside_transaction(self, post):
        verify_response = Mock()
        verify_response.raise_for_status.return_value = None
        verify_response.json.return_value = {'success': True}
        post.return_value = verify_response
        payload = {
            'username': 'ConcurrentName',
            'email': 'new@example.com',
            'first_name': 'New',
            'last_name': 'Student',
            'password': 'A-strong-test-password-2026',
            'password2': 'A-strong-test-password-2026',
            'captcha_answer': 'captcha',
        }
        serializer = RegisterSerializer(data=payload)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        make_user('ConcurrentName', email='other@example.com')

        with self.assertRaises(DRFValidationError):
            serializer.save()

    def test_meme_requires_nonblank_title_and_image(self):
        serializer = MemeOfWeekSerializer(
            data={'title': ' ', 'image': None},
            context={'request': APIRequestFactory().post('/api/memes/')},
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn('title', serializer.errors)
        self.assertIn('image', serializer.errors)

    @override_settings(MAX_MEME_UPLOAD_SIZE=10)
    def test_meme_size_validator_runs(self):
        # A valid tiny PNG is still over the deliberately tiny test limit.
        png = base64.b64decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwC'
            'AAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='
        )
        image = SimpleUploadedFile(
            'tiny.png',
            png,
            content_type='image/png',
        )
        serializer = MemeOfWeekSerializer(
            data={'title': 'Tiny', 'image': image},
            context={'request': APIRequestFactory().post('/api/memes/')},
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn('image', serializer.errors)

    @override_settings(MAX_IMAGE_UPLOAD_SIZE=4)
    def test_all_non_meme_image_fields_have_model_size_validator(self):
        image = SimpleUploadedFile(
            'large.png',
            b'12345',
            content_type='image/png',
        )
        image_fields = (
            Posts._meta.get_field('banner'),
            PollQuestion._meta.get_field('image'),
            PollOption._meta.get_field('image'),
            PostImage._meta.get_field('image'),
        )

        for field in image_fields:
            with self.subTest(field=f'{field.model.__name__}.{field.name}'):
                self.assertIn(validate_image_upload_size, field.validators)
                with self.assertRaises(ValidationError):
                    field.run_validators(image)

    @override_settings(MAX_DOCUMENT_UPLOAD_SIZE=4)
    def test_document_model_validates_extension_and_size(self):
        field = PostDocument._meta.get_field('file')
        self.assertIn(validate_document_upload_size, field.validators)

        with self.assertRaises(ValidationError):
            field.run_validators(
                SimpleUploadedFile('malware.exe', b'x')
            )
        with self.assertRaises(ValidationError):
            field.run_validators(
                SimpleUploadedFile('large.pdf', b'12345')
            )

    def test_public_text_payloads_have_explicit_bounds(self):
        comment = CommentSerializer(data={'content': 'x' * 5001})
        contact = ContactSubmissionSerializer(data={
            'name': 'Student',
            'email': 'student@example.com',
            'reason': 'general',
            'message': 'x' * 5001,
        })
        song = BellSongSuggestionSerializer(data={
            'title': 'Song',
            'link': 'https://youtu.be/dQw4w9WgXcQ',
            'slot': 'morning',
            'note': 'x' * 1001,
        })

        self.assertFalse(comment.is_valid())
        self.assertIn('content', comment.errors)
        self.assertFalse(contact.is_valid())
        self.assertIn('message', contact.errors)
        self.assertFalse(song.is_valid())
        self.assertIn('note', song.errors)


class PreservationAndCompatibilityTests(TestCase):
    def test_deleting_user_preserves_authored_content(self):
        user = make_user()
        post = public_post(user, 'preserved')
        terms = TermsOfService.objects.create(user=user, content='Terms')
        privacy = PrivacyPolicy.objects.create(user=user, content='Privacy')
        comment = Comments.objects.create(
            user=user,
            post=post,
            content='Comment',
        )
        now = timezone.now()
        question = PollQuestion.objects.create(
            title='Preserved answer',
            start_date=now,
            end_date=now + timedelta(days=1),
        )
        option = PollOption.objects.create(
            question=question,
            text='Answer',
            key='a',
        )
        answer = PollAnswer.objects.create(
            user=user,
            question=question,
            selected_option=option,
        )

        user.delete()

        post.refresh_from_db()
        terms.refresh_from_db()
        privacy.refresh_from_db()
        comment.refresh_from_db()
        answer.refresh_from_db()
        self.assertIsNone(post.author)
        self.assertIsNone(terms.user)
        self.assertIsNone(privacy.user)
        self.assertIsNone(comment.user)
        self.assertIsNone(answer.user)

    def test_category_with_posts_is_protected(self):
        user = make_user()
        post = public_post(user, 'protected-category')

        with self.assertRaises(ProtectedError):
            post.category.delete()

    def test_consent_record_is_top_level_with_legacy_alias(self):
        self.assertIs(Cookie.ConsentRecord, ConsentRecord)
        record = ConsentRecord.objects.create(consent_status='INFORMED')
        self.assertEqual(record._meta.model_name, 'consentrecord')

    def test_meme_upload_path_handles_deleted_or_missing_owner(self):
        path = meme_upload_path(MemeOfWeek(), 'meme.png')

        self.assertEqual(path, 'memes/anonymous/meme.png')

    def test_site_settings_singleton_is_enforced_by_database(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SiteSettings.objects.bulk_create([
                    SiteSettings(),
                    SiteSettings(),
                ])

    def test_notification_html_escapes_raw_user_html(self):
        notification = Notification(
            text='<script>alert(1)</script>\n[x](javascript:alert(2))'
        )

        html = NotificationSerializer(notification).data['html_text']

        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertNotIn('<a ', html)
        self.assertIn('[x](javascript:alert(2))', html)
        self.assertIn('<br>', html)


class MaintenanceModeTests(TestCase):
    def setUp(self):
        SiteSettings.objects.create(maintenance_mode=True)
        self.user = make_user()
        public_post(self.user, 'maintenance')

    def test_non_staff_content_request_is_blocked(self):
        response = APIClient().get('/api/posts/')

        self.assertEqual(response.status_code, 503)

    def test_staff_can_access_content_during_maintenance(self):
        staff = make_user('staff')
        staff.is_staff = True
        staff.save(update_fields=['is_staff'])
        client = APIClient()
        client.force_authenticate(staff)

        response = client.get('/api/posts/')

        self.assertEqual(response.status_code, 200)


class ProxyAddressTests(TestCase):
    @override_settings(TRUSTED_PROXY_IPS=('10.0.0.10', '10.0.0.11'))
    def test_trusted_proxy_chain_returns_first_untrusted_hop(self):
        request = APIRequestFactory().get(
            '/',
            REMOTE_ADDR='10.0.0.10',
            HTTP_X_FORWARDED_FOR='198.51.100.40, 10.0.0.11',
        )

        self.assertEqual(get_client_ip(request), '198.51.100.40')

    @override_settings(TRUSTED_PROXY_IPS=('10.0.0.10',))
    def test_untrusted_peer_cannot_spoof_forwarded_header(self):
        request = APIRequestFactory().get(
            '/',
            REMOTE_ADDR='203.0.113.9',
            HTTP_X_FORWARDED_FOR='198.51.100.40',
        )

        self.assertEqual(get_client_ip(request), '203.0.113.9')

    @override_settings(TRUSTED_PROXY_IPS=('10.0.0.10', '10.0.0.11'))
    def test_all_throttle_classes_use_trusted_proxy_resolution(self):
        trusted_request = APIRequestFactory().get(
            '/',
            REMOTE_ADDR='10.0.0.10',
            HTTP_X_FORWARDED_FOR='198.51.100.40, 10.0.0.11',
        )
        untrusted_request = APIRequestFactory().get(
            '/',
            REMOTE_ADDR='203.0.113.9',
            HTTP_X_FORWARDED_FOR='198.51.100.40',
        )

        for throttle_class in (
            TrustedProxyAnonRateThrottle,
            TrustedProxyUserRateThrottle,
            TrustedProxyScopedRateThrottle,
        ):
            with self.subTest(throttle=throttle_class.__name__):
                throttle = throttle_class()
                self.assertEqual(
                    throttle.get_ident(trusted_request),
                    '198.51.100.40',
                )
                self.assertEqual(
                    throttle.get_ident(untrusted_request),
                    '203.0.113.9',
                )


class ConsentNetworkMetadataTests(TestCase):
    @override_settings(TRUSTED_PROXY_IPS=('127.0.0.1',))
    @patch('blog.views.lookup_network_operator', return_value=('A1 Bulgaria EAD', 8717))
    def test_consent_record_stores_local_asn_lookup(self, lookup):
        response = APIClient().post(
            '/api/consent/',
            {'consent_status': 'ACCEPTED', 'policy_version': 'v1.0'},
            format='json',
            REMOTE_ADDR='127.0.0.1',
            HTTP_X_FORWARDED_FOR='198.51.100.40',
        )

        self.assertEqual(response.status_code, 201)
        record = ConsentRecord.objects.get()
        self.assertEqual(record.ip_address, '198.51.100.40')
        self.assertEqual(record.network_operator, 'A1 Bulgaria EAD')
        self.assertEqual(record.network_asn, 8717)
        lookup.assert_called_once_with('198.51.100.40')
