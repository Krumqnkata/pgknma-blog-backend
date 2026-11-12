"""AI help for moderators: flags possibly inappropriate student content.

It only records an AIContentCheck. Nothing is hidden, deleted or rejected.
"""

import json
import logging
import re
from datetime import timedelta

from django.contrib.contenttypes.models import ContentType
from django.db.models import Q
from django.utils import timezone

from blog.models import AIContentCheck, BellSongSuggestion, Comments, MemeOfWeek

from . import config, prompts
from .client import AIQuotaExceeded, AIUnavailable, run_chat

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RECENT_DAYS = 7
MAX_TEXT_CHARS = 3_000
_VALID_CATEGORIES = {code for code, _ in AIContentCheck.CATEGORY_CHOICES}
_JSON_OBJECT = re.compile(r'\{.*\}', re.DOTALL)


def _comment_text(comment):
    return comment.content or ''


def _song_text(song):
    return '\n'.join(part for part in (song.title, song.note) if part)


def _meme_text(meme):
    return meme.title or ''


# (model, date field, text builder)
CHECKED_MODELS = (
    (Comments, 'created_at', _comment_text),
    (BellSongSuggestion, 'submitted_at', _song_text),
    (MemeOfWeek, 'created_at', _meme_text),
)


class ModerationParseError(ValueError):
    pass


def parse_moderation_answer(text):
    """Read the model's JSON defensively. Raises ModerationParseError."""
    match = _JSON_OBJECT.search(text or '')
    if not match:
        raise ModerationParseError('No JSON object in the answer.')
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise ModerationParseError('Invalid JSON.') from exc
    if not isinstance(data, dict) or not isinstance(data.get('flagged'), bool):
        raise ModerationParseError('Missing "flagged".')
    category = str(data.get('category') or '').strip().lower()
    if not data['flagged']:
        category = ''
    elif category not in _VALID_CATEGORIES:
        category = 'other'
    reason = str(data.get('reason') or '').strip()[:300] if data['flagged'] else ''
    return data['flagged'], category, reason


def check_text(text, model):
    """Return (verdict, category, reason). Raises AIError subclasses."""
    result = run_chat(
        model=model,
        messages=[
            {'role': 'system', 'content': prompts.MODERATION_SYSTEM},
            {'role': 'user', 'content': f'<<<ТЕКСТ>>>\n{text[:MAX_TEXT_CHARS]}\n<<<КРАЙ>>>'},
        ],
        max_tokens=120,
        temperature=0,
    )
    flagged, category, reason = parse_moderation_answer(result.text)
    verdict = AIContentCheck.VERDICT_FLAGGED if flagged else AIContentCheck.VERDICT_OK
    return verdict, category, reason


def check_object(obj, text_builder, model):
    """Check one object and store the result. AIQuotaExceeded propagates."""
    content_type = ContentType.objects.get_for_model(obj)
    check, _ = AIContentCheck.objects.get_or_create(
        content_type=content_type,
        object_id=obj.pk,
        defaults={'verdict': AIContentCheck.VERDICT_ERROR},
    )
    text = text_builder(obj).strip()
    check.attempts += 1
    check.checked_at = timezone.now()
    check.model_name = model
    if not text:
        check.verdict, check.category, check.reason = AIContentCheck.VERDICT_OK, '', ''
        check.save()
        return check
    try:
        check.verdict, check.category, check.reason = check_text(text, model)
    except AIQuotaExceeded:
        # Do not count a quota stop as a failed attempt.
        check.attempts -= 1
        check.save()
        raise
    except (AIUnavailable, ModerationParseError) as exc:
        logger.warning('AI moderation failed for %s #%s: %s', content_type, obj.pk, exc)
        check.verdict, check.category, check.reason = AIContentCheck.VERDICT_ERROR, '', ''
    check.save()
    return check


def pending_objects(model, date_field):
    """Recent objects without a check, or with a failed check that may be retried."""
    since = timezone.now() - timedelta(days=RECENT_DAYS)
    content_type = ContentType.objects.get_for_model(model)
    finished_ids = AIContentCheck.objects.filter(content_type=content_type).filter(
        ~Q(verdict=AIContentCheck.VERDICT_ERROR) | Q(attempts__gte=MAX_ATTEMPTS)
    ).values('object_id')
    return (
        model.objects.filter(**{f'{date_field}__gte': since})
        .exclude(pk__in=finished_ids)
        .order_by(date_field)
    )


def check_pending_content(limit):
    """Check up to `limit` objects across all models. Returns (checked, flagged)."""
    ai_model = config.student_text_model()
    checked = flagged = 0
    for model, date_field, text_builder in CHECKED_MODELS:
        for obj in pending_objects(model, date_field).iterator():
            if checked >= limit:
                return checked, flagged
            check = check_object(obj, text_builder, ai_model)
            checked += 1
            flagged += check.verdict == AIContentCheck.VERDICT_FLAGGED
    return checked, flagged
