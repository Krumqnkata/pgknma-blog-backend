import io
import logging
import uuid
from urllib.parse import urljoin, urlparse

import requests
from PIL import Image, ImageOps, UnidentifiedImageError
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.templatetags.static import static
from django.utils import timezone


logger = logging.getLogger(__name__)

AVATAR_OUTPUT_SIZE = 512
AVATAR_OUTPUT_QUALITY = 85
AVATAR_MAX_PIXELS = 25_000_000
AVATAR_ALLOWED_FORMATS = {'JPEG', 'PNG', 'WEBP'}
AVATAR_ALLOWED_MIME_TYPES = ('image/jpeg', 'image/png', 'image/webp')

AVATAR_PRESETS = (
    ('code', 'Код'),
    ('terminal', 'Терминал'),
    ('robot', 'Робот'),
    ('calculator', 'Калкулатор'),
    ('geometry', 'Геометрия'),
    ('atom', 'Наука'),
    ('books', 'Книги'),
    ('pencil', 'Творчество'),
    ('music', 'Музика'),
    ('gamepad', 'Игри'),
    ('rocket', 'Ракета'),
    ('idea', 'Идея'),
)
AVATAR_PRESET_IDS = frozenset(preset_id for preset_id, _ in AVATAR_PRESETS)


class AvatarImportError(Exception):
    """A remote avatar could not be imported without affecting sign-in."""


def profile_avatar_upload_path(instance, filename):
    """Keep avatars grouped by user and use a cache-busting filename."""

    return f'profiles/{instance.user_id}/avatar-{uuid.uuid4().hex}.webp'


def validate_avatar_upload_size(uploaded_file):
    max_size = getattr(settings, 'MAX_AVATAR_UPLOAD_SIZE', 5 * 1024 * 1024)
    if uploaded_file and uploaded_file.size > max_size:
        max_size_mb = max_size / (1024 * 1024)
        raise ValidationError(
            f'Профилната снимка не може да е по-голяма от {max_size_mb:g} MB.'
        )


def avatar_preset_url(preset_id):
    if preset_id not in AVATAR_PRESET_IDS:
        return None
    return static(f'blog/avatars/{preset_id}.svg')


def avatar_options_payload():
    return {
        'max_upload_bytes': getattr(
            settings,
            'MAX_AVATAR_UPLOAD_SIZE',
            5 * 1024 * 1024,
        ),
        'output_size': AVATAR_OUTPUT_SIZE,
        'allowed_mime_types': list(AVATAR_ALLOWED_MIME_TYPES),
        'presets': [
            {
                'id': preset_id,
                'label': label,
                'url': avatar_preset_url(preset_id),
            }
            for preset_id, label in AVATAR_PRESETS
        ],
    }


def _avatar_validation_error(message):
    return ValidationError({'avatar': [message]})


def normalize_avatar(uploaded_file):
    """Validate and re-encode an image as a metadata-free square WebP."""

    validate_avatar_upload_size(uploaded_file)
    try:
        uploaded_file.seek(0)
        with Image.open(uploaded_file) as image:
            source_format = (image.format or '').upper()
            if source_format not in AVATAR_ALLOWED_FORMATS:
                raise _avatar_validation_error(
                    'Разрешени са само JPEG, PNG и WebP изображения.'
                )
            if getattr(image, 'is_animated', False) or getattr(image, 'n_frames', 1) > 1:
                raise _avatar_validation_error(
                    'Анимирани профилни снимки не са разрешени.'
                )
            width, height = image.size
            if width < 1 or height < 1 or width * height > AVATAR_MAX_PIXELS:
                raise _avatar_validation_error(
                    'Изображението е с прекалено големи размери.'
                )

            image.load()
            image = ImageOps.exif_transpose(image)
            has_alpha = image.mode in {'RGBA', 'LA'} or (
                image.mode == 'P' and 'transparency' in image.info
            )
            image = image.convert('RGBA' if has_alpha else 'RGB')
            image = ImageOps.fit(
                image,
                (AVATAR_OUTPUT_SIZE, AVATAR_OUTPUT_SIZE),
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )

            output = io.BytesIO()
            image.save(
                output,
                format='WEBP',
                quality=AVATAR_OUTPUT_QUALITY,
                method=6,
            )
    except ValidationError:
        raise
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError, ValueError):
        raise _avatar_validation_error(
            'Файлът не е валидно или поддържано изображение.'
        )
    finally:
        try:
            uploaded_file.seek(0)
        except (AttributeError, OSError, ValueError):
            pass

    output.seek(0)
    return ContentFile(
        output.read(),
        name=f'avatar-{uuid.uuid4().hex}.webp',
    )


def set_profile_avatar(profile, uploaded_file, *, source, user_selected):
    normalized = normalize_avatar(uploaded_file)
    profile.avatar = normalized
    profile.avatar_source = source
    profile.avatar_preset = ''
    profile.avatar_user_selected = user_selected
    profile.avatar_updated_at = timezone.now()
    profile.save(
        update_fields=(
            'avatar',
            'avatar_source',
            'avatar_preset',
            'avatar_user_selected',
            'avatar_updated_at',
        )
    )
    return profile


def set_profile_preset(profile, preset_id):
    if preset_id not in AVATAR_PRESET_IDS:
        raise ValidationError({'preset_id': ['Невалиден готов аватар.']})
    profile.avatar = None
    profile.avatar_source = profile.AvatarSource.PRESET
    profile.avatar_preset = preset_id
    profile.avatar_user_selected = True
    profile.avatar_updated_at = timezone.now()
    profile.save(
        update_fields=(
            'avatar',
            'avatar_source',
            'avatar_preset',
            'avatar_user_selected',
            'avatar_updated_at',
        )
    )
    return profile


def reset_profile_avatar(profile, *, user_selected=True):
    profile.avatar = None
    profile.avatar_source = profile.AvatarSource.INITIALS
    profile.avatar_preset = ''
    profile.avatar_user_selected = user_selected
    profile.avatar_updated_at = timezone.now()
    profile.save(
        update_fields=(
            'avatar',
            'avatar_source',
            'avatar_preset',
            'avatar_user_selected',
            'avatar_updated_at',
        )
    )
    return profile


def _is_allowed_google_avatar_url(url):
    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or '').lower().rstrip('.')
        port = parsed.port
    except (TypeError, ValueError):
        return False
    return (
        parsed.scheme == 'https'
        and port in (None, 443)
        and (
            hostname == 'googleusercontent.com'
            or hostname.endswith('.googleusercontent.com')
        )
    )


def download_google_avatar(picture_url):
    if not _is_allowed_google_avatar_url(picture_url):
        raise AvatarImportError('Google върна неподдържан адрес за снимката.')

    max_size = getattr(settings, 'MAX_AVATAR_UPLOAD_SIZE', 5 * 1024 * 1024)
    current_url = picture_url
    response = None
    try:
        for _ in range(4):
            response = requests.get(
                current_url,
                stream=True,
                timeout=(3, 5),
                allow_redirects=False,
                headers={'User-Agent': 'PGKNMA-Avatar-Importer/1.0'},
            )
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get('Location')
                response.close()
                response = None
                if not location:
                    raise AvatarImportError('Google снимката не може да бъде изтеглена.')
                current_url = urljoin(current_url, location)
                if not _is_allowed_google_avatar_url(current_url):
                    raise AvatarImportError('Google снимката пренасочва към неразрешен адрес.')
                continue
            break
        else:
            raise AvatarImportError('Прекалено много пренасочвания за Google снимката.')

        if response is None or response.status_code != 200:
            raise AvatarImportError('Google снимката не може да бъде изтеглена.')
        content_type = response.headers.get('Content-Type', '').split(';', 1)[0].lower()
        if content_type not in AVATAR_ALLOWED_MIME_TYPES:
            raise AvatarImportError('Google върна неподдържан формат за снимката.')
        try:
            content_length = int(response.headers.get('Content-Length') or 0)
        except (TypeError, ValueError):
            content_length = 0
        if content_length > max_size:
            raise AvatarImportError('Google снимката е прекалено голяма.')

        payload = bytearray()
        for chunk in response.iter_content(64 * 1024):
            if not chunk:
                continue
            payload.extend(chunk)
            if len(payload) > max_size:
                raise AvatarImportError('Google снимката е прекалено голяма.')
    except requests.RequestException as exc:
        raise AvatarImportError('Google снимката не може да бъде изтеглена.') from exc
    finally:
        if response is not None:
            response.close()

    try:
        return normalize_avatar(ContentFile(bytes(payload), name='google-avatar'))
    except ValidationError as exc:
        raise AvatarImportError('Google снимката не е валидно изображение.') from exc


def import_google_avatar_once(user, picture_url):
    """Import once without ever overwriting an explicit user/admin choice."""

    if not picture_url:
        return False

    from .models import UserProfile

    profile, _ = UserProfile.objects.get_or_create(user=user)
    def can_import(current_profile):
        # A system-assigned preset is only a temporary fallback.  It must not
        # prevent a Google user from receiving their actual Google photo later.
        return (
            not current_profile.avatar_user_selected
            and not current_profile.avatar
            and current_profile.avatar_source
            in {
                UserProfile.AvatarSource.INITIALS,
                UserProfile.AvatarSource.PRESET,
            }
        )

    if not can_import(profile):
        return False

    normalized = download_google_avatar(picture_url)
    with transaction.atomic():
        profile = UserProfile.objects.select_for_update().get(pk=profile.pk)
        if not can_import(profile):
            return False
        profile.avatar = normalized
        profile.avatar_source = UserProfile.AvatarSource.GOOGLE
        profile.avatar_preset = ''
        profile.avatar_user_selected = False
        profile.avatar_updated_at = timezone.now()
        profile.save(
            update_fields=(
                'avatar',
                'avatar_source',
                'avatar_preset',
                'avatar_user_selected',
                'avatar_updated_at',
            )
        )
    return True
