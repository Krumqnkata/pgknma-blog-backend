import io
import tempfile
from unittest.mock import patch

from PIL import Image
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from .avatars import AvatarImportError, import_google_avatar_once, reset_profile_avatar
from .models import Category, Comments, Posts, UserProfile
from .personal_data_export import build_personal_data_export


def image_upload(name='avatar.png', size=(800, 600), color=(92, 45, 145, 255)):
    output = io.BytesIO()
    Image.new('RGBA', size, color).save(output, format='PNG')
    return SimpleUploadedFile(name, output.getvalue(), content_type='image/png')


def normalized_webp(name='google.webp'):
    output = io.BytesIO()
    Image.new('RGB', (512, 512), '#2563eb').save(output, format='WEBP')
    return ContentFile(output.getvalue(), name=name)


class AvatarApiTests(APITestCase):
    def setUp(self):
        self.media_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.media_directory.cleanup)
        self.settings_override = override_settings(
            MEDIA_ROOT=self.media_directory.name,
            MAX_AVATAR_UPLOAD_SIZE=5 * 1024 * 1024,
        )
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.user = User.objects.create_user(
            username='avatar-user',
            email='avatar@example.com',
            password='Strong-test-password-123',
        )
        self.client.force_authenticate(self.user)

    def test_profile_is_created_for_every_new_user(self):
        self.assertTrue(UserProfile.objects.filter(user=self.user).exists())

    def test_upload_is_reencoded_as_square_webp(self):
        response = self.client.put(
            '/api/profile/avatar/',
            {'avatar': image_upload()},
            format='multipart',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        profile = UserProfile.objects.get(user=self.user)
        self.assertEqual(profile.avatar_source, UserProfile.AvatarSource.UPLOAD)
        self.assertTrue(profile.avatar_user_selected)
        self.assertTrue(profile.avatar.name.startswith(f'profiles/{self.user.pk}/'))
        self.assertTrue(profile.avatar.name.endswith('.webp'))
        with Image.open(profile.avatar.path) as stored:
            self.assertEqual(stored.format, 'WEBP')
            self.assertEqual(stored.size, (512, 512))
            self.assertFalse(stored.getexif())
        self.assertEqual(response.data['user']['avatar_source'], 'upload')
        self.assertTrue(response.data['user']['avatar_url'].startswith('/files/'))

    def test_profile_endpoint_returns_server_driven_options(self):
        response = self.client.get('/api/profile/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['user']['avatar_source'], 'initials')
        self.assertEqual(response.data['avatar_options']['output_size'], 512)
        self.assertEqual(len(response.data['avatar_options']['presets']), 12)
        self.assertEqual(
            response.data['avatar_options']['presets'][0]['url'],
            '/static/blog/avatars/code.svg',
        )

    def test_preset_and_reset_to_initials(self):
        preset_response = self.client.put(
            '/api/profile/avatar/',
            {'preset_id': 'robot'},
            format='json',
        )
        self.assertEqual(preset_response.status_code, status.HTTP_200_OK)
        self.assertEqual(preset_response.data['user']['avatar_source'], 'preset')
        self.assertEqual(preset_response.data['user']['avatar_preset'], 'robot')
        self.assertEqual(
            preset_response.data['user']['avatar_url'],
            '/static/blog/avatars/robot.svg',
        )

        reset_response = self.client.delete('/api/profile/avatar/')
        self.assertEqual(reset_response.status_code, status.HTTP_200_OK)
        self.assertEqual(reset_response.data['user']['avatar_source'], 'initials')
        self.assertIsNone(reset_response.data['user']['avatar_url'])
        profile = UserProfile.objects.get(user=self.user)
        self.assertTrue(profile.avatar_user_selected)

    def test_invalid_preset_and_anonymous_access_are_rejected(self):
        invalid = self.client.put(
            '/api/profile/avatar/',
            {'preset_id': 'not-a-preset'},
            format='json',
        )
        self.assertEqual(invalid.status_code, status.HTTP_400_BAD_REQUEST)

        self.client.force_authenticate(user=None)
        anonymous = self.client.get('/api/profile/')
        self.assertEqual(anonymous.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_animated_image_is_rejected(self):
        first = Image.new('RGB', (32, 32), 'red')
        second = Image.new('RGB', (32, 32), 'blue')
        output = io.BytesIO()
        first.save(
            output,
            format='GIF',
            save_all=True,
            append_images=[second],
            duration=100,
            loop=0,
        )
        upload = SimpleUploadedFile(
            'animated.gif',
            output.getvalue(),
            content_type='image/gif',
        )

        response = self.client.put(
            '/api/profile/avatar/',
            {'avatar': upload},
            format='multipart',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(UserProfile.objects.get(user=self.user).avatar)

    def test_comments_posts_and_export_contain_current_avatar(self):
        profile = UserProfile.objects.get(user=self.user)
        profile.avatar_source = UserProfile.AvatarSource.PRESET
        profile.avatar_preset = 'code'
        profile.avatar_user_selected = True
        profile.avatar_updated_at = timezone.now()
        profile.save()
        category = Category.objects.create(full_name='Новини', short_name='news')
        post = Posts.objects.create(
            title='Тестова публикация',
            category=category,
            hook='Тест',
            content='Съдържание',
            author=self.user,
            published=True,
            allowed=True,
        )
        Comments.objects.create(content='Коментар', user=self.user, post=post)

        post_response = self.client.get(f'/api/posts/{post.pk}/')
        comment_response = self.client.get(f'/api/posts/{post.pk}/comments/')
        exported = build_personal_data_export(self.user)

        self.assertEqual(
            post_response.data['author_avatar_url'],
            '/static/blog/avatars/code.svg',
        )
        self.assertEqual(
            comment_response.data[0]['avatar_url'],
            '/static/blog/avatars/code.svg',
        )
        self.assertEqual(exported['account']['avatar']['source'], 'preset')
        self.assertEqual(exported['account']['avatar']['preset'], 'code')

    def test_deactivation_resets_avatar_before_disabling_user(self):
        profile = UserProfile.objects.get(user=self.user)
        profile.avatar_source = UserProfile.AvatarSource.PRESET
        profile.avatar_preset = 'books'
        profile.avatar_user_selected = True
        profile.save()

        response = self.client.put('/api/auth/profile/deactivate/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        profile.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertEqual(profile.avatar_source, UserProfile.AvatarSource.INITIALS)
        self.assertEqual(profile.avatar_preset, '')


class GoogleAvatarImportTests(APITestCase):
    def setUp(self):
        self.media_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.media_directory.cleanup)
        self.settings_override = override_settings(MEDIA_ROOT=self.media_directory.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.user = User.objects.create_user(
            username='google-avatar-user',
            email='google-avatar@example.com',
        )

    @patch('blog.avatars.download_google_avatar')
    def test_google_import_happens_once_and_never_overwrites_choice(self, download):
        download.return_value = normalized_webp()

        imported = import_google_avatar_once(
            self.user,
            'https://lh3.googleusercontent.com/a/test-only-avatar-v1',
        )
        self.assertTrue(imported)
        profile = UserProfile.objects.get(user=self.user)
        self.assertEqual(profile.avatar_source, UserProfile.AvatarSource.GOOGLE)
        self.assertFalse(profile.avatar_user_selected)

        imported_again = import_google_avatar_once(
            self.user,
            'https://lh3.googleusercontent.com/a/test-only-avatar-v2',
        )
        self.assertFalse(imported_again)
        self.assertEqual(download.call_count, 1)

        reset_profile_avatar(profile, user_selected=True)
        imported_after_reset = import_google_avatar_once(
            self.user,
            'https://lh3.googleusercontent.com/a/test-only-avatar-v2',
        )
        self.assertFalse(imported_after_reset)
        self.assertEqual(download.call_count, 1)

    @override_settings(GOOGLE_CLIENT_ID='test-google-client')
    @patch(
        'blog.auth.import_google_avatar_once',
        side_effect=AvatarImportError('temporary failure'),
    )
    @patch('blog.auth.id_token.verify_oauth2_token')
    def test_avatar_failure_never_blocks_google_login(self, verify_token, _import):
        self.user.set_unusable_password()
        self.user.save(update_fields=['password'])
        verify_token.return_value = {
            'iss': 'https://accounts.google.com',
            'sub': 'google-subject-avatar-test',
            'email': self.user.email,
            'email_verified': True,
            'picture': 'https://lh3.googleusercontent.com/a/test-only-avatar-v1',
        }

        response = self.client.post(
            '/api/auth/google/',
            {'credential': 'verified-by-mock'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('access', response.data)
        self.assertEqual(response.data['user']['avatar_source'], 'initials')
