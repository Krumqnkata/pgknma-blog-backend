import tempfile
from io import BytesIO

from PIL import Image
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.forms.models import inlineformset_factory
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils.datastructures import MultiValueDict
from rest_framework.test import APIClient

from .admin import PostImageInlineFormSet
from .forms import (
    PostAdminForm,
    validate_document_size,
    validate_image_size,
)
from .models import Category, PostImage, Posts


class GalleryTestMixin:
    def setUp(self):
        super().setUp()
        self.media_directory = tempfile.TemporaryDirectory()
        self.media_override = override_settings(
            MEDIA_ROOT=self.media_directory.name,
        )
        self.media_override.enable()
        self.addCleanup(self._cleanup_media)
        self.author = User.objects.create_user(
            username='gallery-author',
            password='test-password',
        )
        self.category = Category.objects.create(
            full_name='Галерия',
            short_name='gallery',
        )
        self.post = Posts.objects.create(
            title='Публикация с галерия',
            category=self.category,
            hook='Кратко описание',
            content='Съдържание',
            author=self.author,
            published=True,
            allowed=True,
        )

    def _cleanup_media(self):
        self.media_override.disable()
        self.media_directory.cleanup()

    def image_upload(self, filename, color):
        buffer = BytesIO()
        Image.new('RGB', (4, 4), color=color).save(buffer, format='PNG')
        return SimpleUploadedFile(
            filename,
            buffer.getvalue(),
            content_type='image/png',
        )


class UploadLimitSettingsTests(TestCase):
    @override_settings(
        MAX_IMAGE_UPLOAD_SIZE=2 * 1024 * 1024,
        MAX_DOCUMENT_UPLOAD_SIZE=3 * 1024 * 1024,
    )
    def test_form_labels_and_validators_use_django_settings(self):
        form = PostAdminForm()

        self.assertIn('по 2 MB', form.fields['gallery_images'].label)
        self.assertIn('по 3 MB', form.fields['gallery_documents'].label)
        self.assertEqual(
            form.fields['gallery_images'].widget.attrs['data-max-file-size'],
            str(2 * 1024 * 1024),
        )

        oversized_image = SimpleUploadedFile(
            'large-image.png',
            b'x' * (2 * 1024 * 1024 + 1),
            content_type='image/png',
        )
        oversized_document = SimpleUploadedFile(
            'large-document.pdf',
            b'x' * (3 * 1024 * 1024 + 1),
            content_type='application/pdf',
        )
        with self.assertRaisesMessage(ValidationError, 'до 2 MB'):
            validate_image_size(oversized_image)
        with self.assertRaisesMessage(ValidationError, 'до 3 MB'):
            validate_document_size(oversized_document)


class PostImageModelTests(GalleryTestMixin, TestCase):
    def test_hash_blocks_duplicate_content_inside_same_post(self):
        PostImage.objects.create(
            post=self.post,
            image=self.image_upload('first.png', 'red'),
        )

        with self.assertRaisesMessage(
            ValidationError,
            'Тази снимка вече присъства в галерията.',
        ):
            PostImage.objects.create(
                post=self.post,
                image=self.image_upload('renamed-copy.png', 'red'),
            )

        self.assertEqual(self.post.images.count(), 1)
        self.assertEqual(len(self.post.images.get().file_hash), 64)

    def test_selecting_featured_image_unsets_the_previous_one(self):
        first = PostImage.objects.create(
            post=self.post,
            image=self.image_upload('first.png', 'red'),
            is_featured=True,
        )
        second = PostImage.objects.create(
            post=self.post,
            image=self.image_upload('second.png', 'blue'),
            is_featured=True,
        )

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertFalse(first.is_featured)
        self.assertTrue(second.is_featured)


class PostGalleryFormTests(GalleryTestMixin, TestCase):
    def test_bulk_upload_rejects_existing_image_even_with_new_filename(self):
        PostImage.objects.create(
            post=self.post,
            image=self.image_upload('existing.png', 'green'),
        )
        form = PostAdminForm(
            data={
                'title': self.post.title,
                'category': self.category.pk,
                'hook': self.post.hook,
                'content': self.post.content,
                'published': 'on',
                'allowed': 'on',
            },
            files=MultiValueDict({
                'gallery_images': [
                    self.image_upload('duplicate.png', 'green')
                ]
            }),
            instance=self.post,
        )

        self.assertFalse(form.is_valid())
        self.assertIn('дублира', form.errors['gallery_images'][0])


class PostGalleryInlineTests(GalleryTestMixin, TestCase):
    def test_inline_save_reorders_images_and_backfills_old_hashes(self):
        first = PostImage.objects.create(
            post=self.post,
            image=self.image_upload('first.png', 'red'),
            position=0,
        )
        second = PostImage.objects.create(
            post=self.post,
            image=self.image_upload('second.png', 'blue'),
            position=1,
        )
        PostImage.objects.filter(pk__in=(first.pk, second.pk)).update(
            file_hash=None
        )
        first.refresh_from_db()
        second.refresh_from_db()
        formset_class = inlineformset_factory(
            Posts,
            PostImage,
            formset=PostImageInlineFormSet,
            fields=(
                'image',
                'title',
                'alt_text',
                'is_featured',
                'focus_x',
                'focus_y',
                'position',
            ),
            extra=0,
            can_delete=True,
        )
        formset = formset_class(
            data={
                'images-TOTAL_FORMS': '2',
                'images-INITIAL_FORMS': '2',
                'images-MIN_NUM_FORMS': '0',
                'images-MAX_NUM_FORMS': '1000',
                'images-0-id': str(first.pk),
                'images-0-title': '',
                'images-0-alt_text': '',
                'images-0-focus_x': '50',
                'images-0-focus_y': '50',
                'images-0-position': '1',
                'images-1-id': str(second.pk),
                'images-1-title': 'Водеща',
                'images-1-alt_text': 'Описание',
                'images-1-is_featured': 'on',
                'images-1-focus_x': '60',
                'images-1-focus_y': '40',
                'images-1-position': '0',
            },
            instance=self.post,
            prefix='images',
        )

        self.assertTrue(formset.is_valid(), formset.errors)
        formset.save()

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(
            list(self.post.images.values_list('pk', flat=True)),
            [second.pk, first.pk],
        )
        self.assertFalse(first.is_featured)
        self.assertTrue(second.is_featured)
        self.assertEqual(len(first.file_hash), 64)
        self.assertEqual(len(second.file_hash), 64)


class PostGalleryApiTests(GalleryTestMixin, TestCase):
    def test_api_exposes_rich_gallery_and_keeps_legacy_images(self):
        second = PostImage.objects.create(
            post=self.post,
            image=self.image_upload('second.png', 'blue'),
            title='Втора снимка',
            alt_text='Ученици в компютърен кабинет',
            position=2,
            is_featured=True,
            focus_x=65,
            focus_y=35,
        )
        first = PostImage.objects.create(
            post=self.post,
            image=self.image_upload('first.png', 'red'),
            title='Първа снимка',
            position=1,
        )

        response = APIClient().get(f'/api/posts/{self.post.pk}/')

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload['images']), 2)
        self.assertEqual(
            [item['id'] for item in payload['gallery']],
            [first.pk, second.pk],
        )
        featured = payload['gallery'][1]
        self.assertEqual(featured['title'], 'Втора снимка')
        self.assertEqual(
            featured['alt_text'],
            'Ученици в компютърен кабинет',
        )
        self.assertTrue(featured['is_featured'])
        self.assertEqual(featured['focus_x'], 65)
        self.assertEqual(featured['focus_y'], 35)


class PostGalleryAdminTests(GalleryTestMixin, TestCase):
    def test_post_change_page_contains_gallery_editor_and_preview_script(self):
        superuser = User.objects.create_superuser(
            username='gallery-admin',
            email='gallery-admin@example.com',
            password='test-password',
        )
        PostImage.objects.create(
            post=self.post,
            image=self.image_upload('admin.png', 'purple'),
            alt_text='Тестово изображение',
        )
        self.client.force_login(superuser)

        response = self.client.get(
            reverse('admin:blog_posts_change', args=(self.post.pk,))
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Алтернативен текст')
        self.assertContains(response, 'Водеща снимка')
        self.assertContains(response, 'Преглед на кадрирането')
        self.assertContains(response, 'data-gallery-upload="true"')
        self.assertContains(response, 'blog/admin/post_gallery_upload.js')
