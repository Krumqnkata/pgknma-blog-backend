"""Create missing gallery variants without saving or replacing the original."""

import logging
from dataclasses import dataclass
from uuid import uuid4

from django.db import transaction

from .models import PostImage, calculate_file_sha256
from .post_images import make_post_image_variants


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BackfillResult:
    status: str
    original_bytes: int = 0
    added_bytes: int = 0


def _remove_new_files(files):
    for storage, name in files:
        try:
            storage.delete(name)
        except Exception:
            logger.exception('Could not remove newly generated gallery file %s', name)


def create_missing_variants(image):
    """Use a conditional update so concurrent edits cannot be overwritten.

    This deliberately avoids PostImage.save(): a backfill must not recompute
    legacy hashes, change featured images, or write the original FileField.
    Only files created by this call may be removed on failure.
    """
    if image.display_image and image.thumbnail_image:
        return BackfillResult('already-ready')

    expected = {
        'pk': image.pk,
        'post_id': image.post_id,
        'image': image.image.name,
        'display_image': image.display_image.name,
        'thumbnail_image': image.thumbnail_image.name,
    }
    with image.image.open('rb') as original:
        original_bytes = original.size
        digest = calculate_file_sha256(original)
        variants = make_post_image_variants(original, digest)
    if variants is None:
        return BackfillResult('animated-skipped', original_bytes)

    written = []
    updates = {}
    added_bytes = 0
    try:
        for field_name, content in zip(
            ('display_image', 'thumbnail_image'), variants,
        ):
            field = getattr(image, field_name)
            if field:
                continue
            # Separate runs must never overwrite one another's generated files.
            field.save(f'{uuid4().hex}.webp', content, save=False)
            written.append((field.storage, field.name))
            updates[field_name] = field.name
            added_bytes += content.size

        # Encoding and file I/O stay outside the short DB transaction. A second
        # worker or an admin edit wins safely if the row changed meanwhile.
        with transaction.atomic():
            updated = PostImage.objects.filter(**expected).update(**updates)
    except Exception:
        _remove_new_files(written)
        raise

    if not updated:
        _remove_new_files(written)
        return BackfillResult('changed-skipped', original_bytes)
    return BackfillResult('optimized', original_bytes, added_bytes)
