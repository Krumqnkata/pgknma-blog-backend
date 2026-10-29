"""Seasonal and holiday header logos: date rules, upload checks and resolving."""

import calendar
from datetime import date, datetime, time, timedelta
from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.utils import timezone
from PIL import Image, UnidentifiedImageError

HEADER_LOGO_MAX_DIMENSION = 1024
HEADER_LOGO_CACHE_KEY = 'header-logo:{day}'


def validate_header_logo_file(image):
    """Keep new header logo uploads small and decodable; the header shows ~40px."""
    if not image or getattr(image, '_committed', True):
        return
    max_size = getattr(settings, 'MAX_HEADER_LOGO_UPLOAD_SIZE', 2 * 1024 * 1024)
    if image.size > max_size:
        raise ValidationError(
            f'Логото не може да е по-голямо от {max_size / (1024 * 1024):g} MB.'
        )
    position = image.tell() if hasattr(image, 'tell') else None
    try:
        image.seek(0)
        with Image.open(image) as decoded:
            width, height = decoded.size
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise ValidationError('Файлът не е валидно изображение.') from error
    finally:
        if position is not None:
            image.seek(position)
    if max(width, height) > HEADER_LOGO_MAX_DIMENSION:
        raise ValidationError(
            f'Логото трябва да е до {HEADER_LOGO_MAX_DIMENSION}×{HEADER_LOGO_MAX_DIMENSION} px '
            f'(сега е {width}×{height} px).'
        )


def make_still_frame(image_field):
    """Return a PNG of the first frame of an animated image, or None if static."""
    if not image_field:
        return None
    try:
        image_field.open('rb')
        image_field.seek(0)
        with Image.open(image_field) as decoded:
            if not getattr(decoded, 'is_animated', False):
                return None
            decoded.seek(0)
            frame = decoded.convert('RGBA')
    except (UnidentifiedImageError, OSError, ValueError):
        return None
    output = BytesIO()
    frame.save(output, format='PNG', optimize=True)
    return ContentFile(output.getvalue(), name=f'{Path(image_field.name).stem}-still.png')


def orthodox_easter(year):
    """Orthodox Easter (Gregorian date) via the Julian computus; valid 1900-2099."""
    a = year % 4
    b = year % 7
    c = year % 19
    d = (19 * c + 15) % 30
    e = (2 * a + 4 * b - d + 34) % 7
    month, day = divmod(d + e + 114, 31)
    return date(year, month, day + 1) + timedelta(days=13)


def month_day(year, month, day):
    """Build a date, clamping e.g. 29 February to the last day of the month."""
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def logo_periods(logo, around):
    """Yield the concrete (start, end) periods of a logo near the given date."""
    if logo.date_mode == logo.DateMode.ONCE:
        if logo.start_date and logo.end_date:
            yield logo.start_date, logo.end_date
        return
    for year in (around.year - 1, around.year, around.year + 1):
        if logo.date_mode == logo.DateMode.EASTER:
            easter = orthodox_easter(year)
            yield (
                easter + timedelta(days=logo.easter_offset_start),
                easter + timedelta(days=logo.easter_offset_end),
            )
        elif None not in (logo.start_month, logo.start_day, logo.end_month, logo.end_day):
            start = month_day(year, logo.start_month, logo.start_day)
            end = month_day(year, logo.end_month, logo.end_day)
            if end < start:
                end = month_day(year + 1, logo.end_month, logo.end_day)
            yield start, end


def logo_period_on(logo, day):
    """Return the period that contains ``day`` or None."""
    for start, end in logo_periods(logo, day):
        if start <= day <= end:
            return start, end
    return None


def logo_rank(logo, period):
    """Holidays beat seasons, then higher priority, then the shorter period."""
    start, end = period
    return (logo.kind == logo.Kind.HOLIDAY, logo.priority, -(end - start).days)


def logos_on(day, queryset=None):
    """All active logos with an image covering ``day``, best match first."""
    from .models import HeaderLogo

    if queryset is None:
        queryset = HeaderLogo.objects.filter(is_active=True).exclude(image='')
    matches = []
    for logo in queryset:
        period = logo_period_on(logo, day)
        if period:
            matches.append((logo_rank(logo, period), logo))
    matches.sort(key=lambda match: match[0], reverse=True)
    return [logo for _, logo in matches]


def header_logo_payload(logo):
    if logo is None:
        return None
    still = logo.static_image or logo.image_still
    return {
        'image_url': logo.image.url,
        'static_image_url': still.url if still else None,
        'greeting': logo.greeting,
        'alt': logo.alt_text or logo.greeting or f'Лого на ПГКНМА – {logo.name}',
        'effect': logo.effect,
    }


def current_header_logo():
    """Today's header logo payload, cached until local midnight."""
    now = timezone.localtime()
    key = HEADER_LOGO_CACHE_KEY.format(day=now.date().isoformat())
    cached = cache.get(key)
    if cached is not None:
        return cached or None
    matches = logos_on(now.date())
    payload = header_logo_payload(matches[0] if matches else None)
    midnight = timezone.make_aware(
        datetime.combine(now.date() + timedelta(days=1), time.min),
        now.tzinfo,
    )
    cache.set(key, payload or {}, max(60, int((midnight - now).total_seconds())))
    return payload


def clear_header_logo_cache():
    cache.delete(HEADER_LOGO_CACHE_KEY.format(day=timezone.localdate().isoformat()))
