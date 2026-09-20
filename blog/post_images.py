"""Bounded, metadata-free display variants for newly uploaded post photos."""

from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile


MAX_SOURCE_PIXELS = 50_000_000
DISPLAY_EDGE = 2048
THUMBNAIL_EDGE = 480


def make_post_image_variants(source, file_hash):
    """Decode once; retain the source and return two aspect-preserving WebPs."""
    try:
        source.seek(0)
        with Image.open(source) as opened:
            if opened.width * opened.height > MAX_SOURCE_PIXELS:
                raise ValidationError({
                    'image': 'Изображението е с прекалено големи размери.'
                })
            # An animated original must not silently turn into a still image.
            if getattr(opened, 'is_animated', False):
                return None
            # JPEG's decoder can avoid materializing every source pixel.
            if opened.format == 'JPEG':
                opened.draft('RGB', (DISPLAY_EDGE, DISPLAY_EDGE))
            opened.load()
            ImageOps.exif_transpose(opened, in_place=True)
            has_alpha = opened.mode in {'RGBA', 'LA'} or (
                opened.mode == 'P' and 'transparency' in opened.info
            )
            normalized = opened.convert('RGBA' if has_alpha else 'RGB')

        result = []
        normalized.thumbnail((DISPLAY_EDGE, DISPLAY_EDGE), Image.Resampling.LANCZOS)
        for label, edge, quality in (
            ('display', DISPLAY_EDGE, 84),
            ('thumbnail', THUMBNAIL_EDGE, 78),
        ):
            if label == 'thumbnail':
                normalized.thumbnail((edge, edge), Image.Resampling.LANCZOS)
            output = BytesIO()
            # No EXIF/XMP is passed to Pillow: GPS and camera metadata stay
            # only in the unchanged original, never in the public variants.
            normalized.save(output, format='WEBP', quality=quality, method=4)
            result.append(ContentFile(
                output.getvalue(), name=f'{file_hash[:32]}-{label}.webp'
            ))
        return tuple(result)
    except ValidationError:
        raise
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValidationError({
            'image': 'Файлът не е валидно или поддържано изображение.'
        }) from exc
    finally:
        try:
            source.seek(0)
        except (AttributeError, OSError, ValueError):
            pass
