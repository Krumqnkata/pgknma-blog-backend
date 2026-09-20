from io import BytesIO, StringIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError
from django.test import TestCase

from .models import PostImage
from .post_image_backfill import create_missing_variants
from .test_gallery import GalleryTestMixin


class PostImageBackfillTests(GalleryTestMixin, TestCase):
    def legacy_image(self, name='old.png', color='orange', post=None):
        with patch('blog.models.make_post_image_variants', return_value=None):
            image = PostImage.objects.create(
                post=post or self.post, image=self.image_upload(name, color),
                title='Keep this caption', position=7, focus_x=35,
            )
        PostImage.objects.filter(pk=image.pk).update(file_hash=None)
        image.refresh_from_db()
        return image

    def files(self):
        return {
            path.relative_to(self.media_directory.name): path.read_bytes()
            for path in Path(self.media_directory.name).rglob('*') if path.is_file()
        }

    def run_command(self, **options):
        stdout, stderr = StringIO(), StringIO()
        call_command('optimize_post_images', stdout=stdout, stderr=stderr, pause=0, **options)
        return stdout.getvalue(), stderr.getvalue()

    def test_default_dry_run_never_processes_or_changes_files(self):
        image = self.legacy_image()
        before = self.files()
        with patch('blog.management.commands.optimize_post_images.create_missing_variants') as process:
            output, _ = self.run_command()
        process.assert_not_called()
        self.assertIn('DRY RUN', output)
        self.assertIn(f'Would process image={image.pk}', output)
        self.assertEqual(self.files(), before)
        image.refresh_from_db()
        self.assertFalse(image.display_image)
        self.assertFalse(image.thumbnail_image)

    def test_apply_preserves_original_metadata_and_is_resumable(self):
        image = self.legacy_image()
        original_name = image.image.name
        original_bytes = Path(image.image.path).read_bytes()
        original_mtime = Path(image.image.path).stat().st_mtime_ns
        output, _ = self.run_command(apply=True)
        self.assertIn('optimized=1', output)
        image.refresh_from_db()
        self.assertEqual(image.image.name, original_name)
        self.assertEqual(Path(image.image.path).read_bytes(), original_bytes)
        self.assertEqual(Path(image.image.path).stat().st_mtime_ns, original_mtime)
        self.assertIsNone(image.file_hash)
        self.assertEqual((image.title, image.position, image.focus_x), ('Keep this caption', 7, 35))
        for field in (image.display_image, image.thumbnail_image):
            with field.open('rb') as source, Image.open(source) as variant:
                self.assertEqual(variant.format, 'WEBP')
        first_run_files = self.files()
        with patch('blog.management.commands.optimize_post_images.create_missing_variants') as process:
            output, _ = self.run_command(apply=True)
        process.assert_not_called()
        self.assertIn('this batch: 0', output)
        self.assertEqual(self.files(), first_run_files)

    def test_limit_post_filter_and_cursor(self):
        first = self.legacy_image('first.png', 'red')
        second = self.legacy_image('second.png', 'blue')
        other_post = type(self.post).objects.create(title='Other', author=self.author)
        other = self.legacy_image('other.png', 'green', post=other_post)

        output, _ = self.run_command(apply=True, post_id=self.post.pk, limit=1)
        self.assertIn(f'--after-id {first.pk}', output)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertTrue(first.thumbnail_image)
        self.assertFalse(second.thumbnail_image)
        self.run_command(apply=True, post_id=self.post.pk, after_id=first.pk, limit=1)
        second.refresh_from_db()
        other.refresh_from_db()
        self.assertTrue(second.thumbnail_image)
        self.assertFalse(other.thumbnail_image)

    def test_existing_variant_is_preserved_when_only_thumbnail_is_missing(self):
        image = self.legacy_image()
        create_missing_variants(image)
        image.refresh_from_db()
        existing_name = image.display_image.name
        existing_bytes = Path(image.display_image.path).read_bytes()
        image.thumbnail_image.storage.delete(image.thumbnail_image.name)
        PostImage.objects.filter(pk=image.pk).update(thumbnail_image='')
        self.run_command(apply=True)
        image.refresh_from_db()
        self.assertEqual(image.display_image.name, existing_name)
        self.assertEqual(Path(image.display_image.path).read_bytes(), existing_bytes)
        self.assertTrue(image.thumbnail_image.storage.exists(image.thumbnail_image.name))

    def test_missing_original_reports_failure_but_processes_next_image(self):
        missing = self.legacy_image('missing.png', 'red')
        good = self.legacy_image('good.png', 'green')
        missing.image.storage.delete(missing.image.name)
        stdout, stderr = StringIO(), StringIO()
        with self.assertRaises(CommandError):
            call_command('optimize_post_images', apply=True, pause=0, stdout=stdout, stderr=stderr)
        self.assertIn(f'image={missing.pk}: FAILED', stderr.getvalue())
        self.assertIn('optimized=1, failed=1', stdout.getvalue())
        missing.refresh_from_db()
        good.refresh_from_db()
        self.assertFalse(missing.display_image)
        self.assertTrue(good.display_image)

    def test_database_error_removes_only_new_variants(self):
        image = self.legacy_image()
        before = self.files()
        with patch('django.db.models.query.QuerySet.update', side_effect=DatabaseError('Write failed')):
            with self.assertRaises(DatabaseError):
                create_missing_variants(image)
        self.assertEqual(self.files(), before)
        image.refresh_from_db()
        self.assertFalse(image.display_image)
        self.assertFalse(image.thumbnail_image)

    def test_second_file_write_failure_cleans_first_variant(self):
        image = self.legacy_image()
        before = self.files()
        storage = image.display_image.storage
        real_save = storage.save
        calls = 0

        def fail_second_save(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('Storage unavailable')
            return real_save(*args, **kwargs)

        with patch.object(storage, 'save', side_effect=fail_second_save):
            with self.assertRaises(OSError):
                create_missing_variants(image)
        self.assertEqual(self.files(), before)
        image.refresh_from_db()
        self.assertFalse(image.display_image)

    def test_stale_worker_cannot_overwrite_completed_variants(self):
        stale = self.legacy_image()
        fresh = PostImage.objects.get(pk=stale.pk)
        create_missing_variants(fresh)
        before = self.files()
        result = create_missing_variants(stale)
        self.assertEqual(result.status, 'changed-skipped')
        self.assertEqual(self.files(), before)
        stale.refresh_from_db()
        self.assertEqual(stale.display_image.name, fresh.display_image.name)
        self.assertEqual(stale.thumbnail_image.name, fresh.thumbnail_image.name)

    def test_animated_original_is_skipped_without_modification(self):
        buffer = BytesIO()
        Image.new('RGB', (4, 4), 'red').save(
            buffer, format='GIF', save_all=True,
            append_images=[Image.new('RGB', (4, 4), 'blue')], duration=100, loop=0,
        )
        PostImage.objects.create(
            post=self.post,
            image=SimpleUploadedFile('old.gif', buffer.getvalue(), content_type='image/gif'),
        )
        before = self.files()
        output, _ = self.run_command(apply=True)
        self.assertIn('animated_skipped=1', output)
        self.assertEqual(self.files(), before)

    def test_deleting_backfilled_image_cleans_original_and_both_variants(self):
        image = self.legacy_image()
        self.run_command(apply=True)
        image.refresh_from_db()
        self.assertEqual(len(self.files()), 3)
        with self.captureOnCommitCallbacks(execute=True):
            image.delete()
        self.assertEqual(self.files(), {})

    def test_invalid_options_are_rejected(self):
        for options in (
            {'limit': 0}, {'after_id': -1}, {'post_id': 0},
            {'pause': float('nan')}, {'pause': -1},
        ):
            with self.subTest(options=options), self.assertRaises(CommandError):
                call_command('optimize_post_images', stdout=StringIO(), stderr=StringIO(), **options)
