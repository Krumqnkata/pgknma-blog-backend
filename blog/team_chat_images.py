"""Validation and metadata-free normalization for private team-chat images."""

import io
from datetime import timedelta
from pathlib import Path

from PIL import Image, ImageOps, ImageSequence, UnidentifiedImageError
from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from .models import TeamChatAttachment


MAX_ATTACHMENTS_PER_MESSAGE = 3
MAX_UPLOADS_PER_HOUR = 10
MAX_IMAGE_PIXELS = 30_000_000
MAX_ANIMATION_WORK_PIXELS = 80_000_000
MAX_ANIMATION_FRAMES = 120
MAX_OUTPUT_DIMENSION = 1920
THUMBNAIL_DIMENSION = 480
ALLOWED_FORMATS = {'JPEG', 'PNG', 'WEBP', 'GIF'}
ALLOWED_MIME_TYPES = ('image/jpeg', 'image/png', 'image/webp', 'image/gif')


class TeamChatImageValidationError(ValueError):
    pass


def max_upload_bytes():
    return getattr(settings, 'TEAM_CHAT_MAX_IMAGE_UPLOAD_SIZE', 5 * 1024 * 1024)


def _fit_within(image, max_dimension):
    result = image.copy()
    result.thumbnail(
        (max_dimension, max_dimension),
        resample=Image.Resampling.LANCZOS,
    )
    return result


def _safe_duration(frame):
    try:
        value = int(frame.info.get('duration') or 100)
    except (TypeError, ValueError):
        value = 100
    return min(10_000, max(20, value))


def _static_webp(image):
    image.load()
    image = ImageOps.exif_transpose(image)
    has_alpha = image.mode in {'RGBA', 'LA'} or (
        image.mode == 'P' and 'transparency' in image.info
    )
    image = image.convert('RGBA' if has_alpha else 'RGB')
    image = _fit_within(image, MAX_OUTPUT_DIMENSION)
    output = io.BytesIO()
    image.save(output, format='WEBP', quality=84, method=6)
    return output, image.size, False, image.copy()


def _animated_webp(image):
    frame_count = int(getattr(image, 'n_frames', 1) or 1)
    width, height = image.size
    if frame_count > MAX_ANIMATION_FRAMES:
        raise TeamChatImageValidationError(
            f'Анимацията може да съдържа най-много {MAX_ANIMATION_FRAMES} кадъра.'
        )
    if width * height * frame_count > MAX_ANIMATION_WORK_PIXELS:
        raise TeamChatImageValidationError('Анимацията е прекалено голяма за обработка.')

    frames = []
    durations = []
    for frame in ImageSequence.Iterator(image):
        converted = frame.convert('RGBA')
        converted = _fit_within(converted, MAX_OUTPUT_DIMENSION)
        frames.append(converted)
        durations.append(_safe_duration(frame))
    if not frames:
        raise TeamChatImageValidationError('Анимацията не съдържа валидни кадри.')

    output = io.BytesIO()
    first, remaining = frames[0], frames[1:]
    first.save(
        output,
        format='WEBP',
        save_all=True,
        append_images=remaining,
        duration=durations,
        loop=min(65_535, max(0, int(image.info.get('loop') or 0))),
        quality=82,
        method=6,
    )
    return output, first.size, True, first.copy()


def normalize_team_chat_image(uploaded_file):
    """Return a sanitized WebP, thumbnail and safe persisted metadata."""

    size = int(getattr(uploaded_file, 'size', 0) or 0)
    if size < 1:
        raise TeamChatImageValidationError('Изберете непразен файл.')
    if size > max_upload_bytes():
        limit_mb = max_upload_bytes() / (1024 * 1024)
        raise TeamChatImageValidationError(
            f'Едно изображение може да бъде най-много {limit_mb:g} MB.'
        )

    try:
        uploaded_file.seek(0)
        with Image.open(uploaded_file) as image:
            source_format = (image.format or '').upper()
            if source_format not in ALLOWED_FORMATS:
                raise TeamChatImageValidationError(
                    'Разрешени са само JPEG, PNG, WebP и GIF изображения.'
                )
            width, height = image.size
            if width < 1 or height < 1 or width * height > MAX_IMAGE_PIXELS:
                raise TeamChatImageValidationError(
                    'Изображението е с прекалено големи размери.'
                )
            is_animated = bool(
                getattr(image, 'is_animated', False)
                and int(getattr(image, 'n_frames', 1) or 1) > 1
            )
            if is_animated:
                output, dimensions, is_animated, thumb_source = _animated_webp(image)
            else:
                output, dimensions, is_animated, thumb_source = _static_webp(image)

        thumb_source = _fit_within(thumb_source.convert('RGBA'), THUMBNAIL_DIMENSION)
        thumbnail = io.BytesIO()
        thumb_source.save(thumbnail, format='WEBP', quality=78, method=6)
    except TeamChatImageValidationError:
        raise
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError, ValueError):
        raise TeamChatImageValidationError(
            'Файлът не е валидно или поддържано изображение.'
        )
    finally:
        try:
            uploaded_file.seek(0)
        except (AttributeError, OSError, ValueError):
            pass

    output.seek(0)
    thumbnail.seek(0)
    output_bytes = output.read()
    thumbnail_bytes = thumbnail.read()
    original_name = Path(str(getattr(uploaded_file, 'name', '') or '')).name[:255]
    return {
        'image': ContentFile(output_bytes, name='image.webp'),
        'thumbnail': ContentFile(thumbnail_bytes, name='thumbnail.webp'),
        'original_name': original_name,
        'original_format': source_format,
        'content_type': 'image/webp',
        'byte_size': len(output_bytes),
        'width': dimensions[0],
        'height': dimensions[1],
        'is_animated': is_animated,
    }


@transaction.atomic
def create_message_attachments(*, message, actor, uploaded_files, actor_name):
    files = list(uploaded_files or ())
    if not files:
        return []
    existing = message.attachments.count()
    if existing + len(files) > MAX_ATTACHMENTS_PER_MESSAGE:
        raise TeamChatImageValidationError(
            f'Към едно съобщение може да има най-много {MAX_ATTACHMENTS_PER_MESSAGE} изображения.'
        )
    since = timezone.now() - timedelta(hours=1)
    recent_count = TeamChatAttachment.objects.filter(
        uploaded_by=actor,
        created_at__gte=since,
    ).count()
    if recent_count + len(files) > MAX_UPLOADS_PER_HOUR:
        raise TeamChatImageValidationError(
            f'Може да качвате най-много {MAX_UPLOADS_PER_HOUR} изображения на час.'
        )

    normalized = [normalize_team_chat_image(item) for item in files]
    created = []
    current = None
    try:
        for item in normalized:
            current = TeamChatAttachment(
                message=message,
                uploaded_by=actor,
                uploaded_by_name=actor_name,
                original_name=item['original_name'],
                original_format=item['original_format'],
                content_type=item['content_type'],
                byte_size=item['byte_size'],
                width=item['width'],
                height=item['height'],
                is_animated=item['is_animated'],
                alt_text=(
                    f'Прикачено изображение: {Path(item["original_name"]).stem}'
                    if item['original_name']
                    else f'Изображение от {actor_name}'
                )[:255],
            )
            current.image = item['image']
            current.thumbnail = item['thumbnail']
            current.save()
            created.append(current)
            current = None
    except Exception:
        if current is not None:
            private_prefix = f'{current.pk}/'
            if (
                current.image
                and current.image.name
                and current.image.name.replace('\\', '/').startswith(private_prefix)
            ):
                current.image.delete(save=False)
            if (
                current.thumbnail
                and current.thumbnail.name
                and current.thumbnail.name.replace('\\', '/').startswith(private_prefix)
            ):
                current.thumbnail.delete(save=False)
        for attachment in created:
            attachment.image.delete(save=False)
            attachment.thumbnail.delete(save=False)
        raise
    return created
