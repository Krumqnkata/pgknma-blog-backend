"""Weekly quiz drafts written by the AI.

Every Thursday (and until Sunday, if a run was missed) the AI writes 3 drafts
for next week. A second request answers each question without seeing the
intended answer; a draft is "confirmed" only when both agree. An admin picks
one draft, or on Monday the first confirmed draft is published automatically
(if that is switched on). Nothing is generated for a week that already has a
poll, so hand-made polls always win.
"""

import json
import logging
import random
import re
from datetime import datetime, time, timedelta

from django.db import transaction
from django.utils import timezone

from blog import team_chat_bot
from blog.models import PollDraft, PollOption, PollQuestion

from . import config, prompts
from .client import AIQuotaExceeded, AIUnavailable, run_chat

logger = logging.getLogger(__name__)

DRAFTS_PER_WEEK = 3
MAX_ATTEMPTS_PER_WEEK = 6  # 3 drafts plus up to 3 failed answers from the model
GENERATION_WEEKDAY = 3  # Thursday (Monday is 0)
KEYS = ('a', 'b', 'c', 'd')
MAX_TITLE_CHARS = 120
MAX_OPTION_CHARS = 255

_CYRILLIC_KEYS = {'а': 'a', 'б': 'b', 'в': 'c', 'с': 'c', 'г': 'd'}


class PollDraftParseError(ValueError):
    """The model did not answer with a usable quiz."""


class PollPublishError(ValueError):
    """The draft cannot become a poll (shown to the admin as a message)."""


# --- Weeks -----------------------------------------------------------------

def week_start(day=None):
    day = day or timezone.localdate()
    return day - timedelta(days=day.weekday())


def next_week_start(day=None):
    return week_start(day) + timedelta(days=7)


def week_bounds(monday):
    """Monday 00:00:00 – Sunday 23:59:59 in school time."""
    start = timezone.make_aware(datetime.combine(monday, time.min))
    end = timezone.make_aware(datetime.combine(monday + timedelta(days=6), time(23, 59, 59)))
    return start, end


def week_has_poll(monday):
    start, end = week_bounds(monday)
    return PollQuestion.objects.filter(start_date__lte=end, end_date__gte=start).exists()


# --- Parsing ---------------------------------------------------------------

def _json_object(text):
    text = (text or '').strip()
    text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text, flags=re.IGNORECASE)
    start, end = text.find('{'), text.rfind('}')
    if start == -1 or end <= start:
        raise PollDraftParseError('Няма JSON в отговора.')
    try:
        data = json.loads(text[start:end + 1])
    except ValueError as exc:
        raise PollDraftParseError('Невалиден JSON.') from exc
    if not isinstance(data, dict):
        raise PollDraftParseError('Отговорът не е JSON обект.')
    return data


def _key(value):
    value = str(value or '').strip().lower().strip('()).:')
    value = _CYRILLIC_KEYS.get(value, value)
    return value if value in KEYS else ''


def _text(value, limit):
    value = re.sub(r'\s+', ' ', str(value or '')).strip()
    return value[:limit].strip()


def parse_poll_draft(text):
    """Return {'title', 'question', 'options': [4 texts], 'correct': index, 'explanation'}."""
    data = _json_object(text)
    title = _text(data.get('title'), MAX_TITLE_CHARS)
    question = str(data.get('question') or '').strip()
    raw_options = data.get('options')
    if isinstance(raw_options, list) and len(raw_options) == 4:
        raw_options = dict(zip(KEYS, raw_options))
    if not isinstance(raw_options, dict):
        raise PollDraftParseError('Липсват отговорите.')
    options = [_text(raw_options.get(key), MAX_OPTION_CHARS) for key in KEYS]
    correct = _key(data.get('correct'))
    if not title or not question:
        raise PollDraftParseError('Липсва заглавие или въпрос.')
    if not all(options) or len({option.lower() for option in options}) != 4:
        raise PollDraftParseError('Отговорите трябва да са 4 различни.')
    if not correct:
        raise PollDraftParseError('Липсва верният отговор.')
    return {
        'title': title,
        'question': question,
        'options': options,
        'correct': KEYS.index(correct),
        'explanation': str(data.get('explanation') or '').strip()[:1000],
    }


def parse_verification(text):
    data = _json_object(text)
    answer = _key(data.get('answer'))
    if not answer:
        raise PollDraftParseError('Проверката не посочи отговор.')
    return answer


# --- AI calls --------------------------------------------------------------

def _recent_titles(monday):
    titles = list(PollQuestion.objects.order_by('-start_date').values_list('title', flat=True)[:30])
    titles += list(
        PollDraft.objects.filter(week_start=monday).exclude(status=PollDraft.Status.FAILED)
        .values_list('title', flat=True)
    )
    return [title for title in titles if title]


def _generation_message(topic, avoid_titles):
    avoid = '\n'.join(f'- {title}' for title in avoid_titles) or '- (няма)'
    return f"Тема: {topic}\n\nВече задавани въпроси (не ги повтаряй):\n{avoid}"


def _verification_message(draft):
    lines = [draft.task_description, '']
    lines += [f'{key.upper()}) {text}' for key, text in draft.options()]
    return '\n'.join(lines)


def verify_draft(draft, model):
    """Answer the question without the intended answer and compare. Saves the draft."""
    try:
        result = run_chat(
            model=model,
            messages=[
                {'role': 'system', 'content': prompts.POLL_VERIFY_SYSTEM},
                {'role': 'user', 'content': _verification_message(draft)},
            ],
            max_tokens=60,
            temperature=0,
        )
        answer = parse_verification(result.text)
    except AIQuotaExceeded:
        draft.verification = PollDraft.Verification.UNCHECKED
        draft.verification_note = 'Дневният лимит на ИИ свърши преди проверката.'
        draft.save(update_fields=['verification', 'verification_note'])
        raise
    except (AIUnavailable, PollDraftParseError) as exc:
        draft.verification = PollDraft.Verification.UNCHECKED
        draft.verification_note = f'Проверката не успя: {exc}'[:255]
    else:
        if answer == draft.correct_key:
            draft.verification = PollDraft.Verification.CONFIRMED
            draft.verification_note = ''
        else:
            draft.verification = PollDraft.Verification.DISPUTED
            draft.verification_note = (
                f'Втората проверка избра {answer.upper()} вместо {draft.correct_key.upper()}. '
                'Проверете ръчно кой отговор е верен.'
            )
    draft.save(update_fields=['verification', 'verification_note'])
    return draft


def generate_draft(monday, *, topic, model):
    """Ask for one quiz, store it and verify it. Failed answers are stored as FAILED."""
    try:
        result = run_chat(
            model=model,
            messages=[
                {'role': 'system', 'content': prompts.POLL_DRAFT_SYSTEM},
                {'role': 'user', 'content': _generation_message(topic, _recent_titles(monday))},
            ],
            max_tokens=700,
            temperature=0.9,
        )
        parsed = parse_poll_draft(result.text)
    except AIQuotaExceeded:
        raise
    except (AIUnavailable, PollDraftParseError) as exc:
        logger.warning('AI poll draft failed for week %s: %s', monday, exc)
        return PollDraft.objects.create(
            week_start=monday,
            status=PollDraft.Status.FAILED,
            verification_note=str(exc)[:255],
            ai_model=model,
        )

    # Models put the right answer first far too often: shuffle the options.
    options = parsed['options']
    correct_text = options[parsed['correct']]
    random.shuffle(options)
    draft = PollDraft.objects.create(
        week_start=monday,
        title=parsed['title'],
        task_description=parsed['question'],
        option_a=options[0],
        option_b=options[1],
        option_c=options[2],
        option_d=options[3],
        correct_key=KEYS[options.index(correct_text)],
        explanation=parsed['explanation'],
        ai_model=result.model,
    )
    return verify_draft(draft, model)


def generate_drafts_for_week(monday, site_settings=None):
    """Fill the week up to DRAFTS_PER_WEEK drafts. Returns (created, failed)."""
    site_settings = site_settings or config.get_site_settings()
    if week_has_poll(monday):
        return 0, 0
    drafts = PollDraft.objects.filter(week_start=monday)
    good = drafts.exclude(status=PollDraft.Status.FAILED).count()
    attempts = drafts.count()
    created = failed = 0
    model = config.public_model(site_settings)
    topic = (site_settings.ai_poll_topic or '').strip() or 'ИТ и технологии'
    while good < DRAFTS_PER_WEEK and attempts < MAX_ATTEMPTS_PER_WEEK:
        draft = generate_draft(monday, topic=topic, model=model)
        attempts += 1
        if draft.status == PollDraft.Status.FAILED:
            failed += 1
        else:
            good += 1
            created += 1
    return created, failed


# --- Publishing ------------------------------------------------------------

def publish_draft(draft):
    """Turn the draft into the poll of its week. Raises PollPublishError."""
    with transaction.atomic():
        draft = PollDraft.objects.select_for_update().get(pk=draft.pk)
        if draft.status == PollDraft.Status.PUBLISHED and draft.poll_id:
            raise PollPublishError('Тази чернова вече е публикувана.')
        if draft.status == PollDraft.Status.FAILED:
            raise PollPublishError('Това е неуспешен опит на ИИ и няма въпрос.')
        draft.full_clean()
        if week_has_poll(draft.week_start):
            raise PollPublishError(
                'За тази седмица вече има анкета. Изтрийте я или сменете датите ѝ, '
                'ако искате да пуснете тази чернова.'
            )
        start, end = week_bounds(draft.week_start)
        poll = PollQuestion.objects.create(
            title=draft.title,
            task_description=draft.task_description,
            start_date=start,
            end_date=end,
        )
        for key, text in draft.options():
            PollOption.objects.create(
                question=poll,
                key=key,
                text=text,
                is_correct=key == draft.correct_key,
            )
        draft.status = PollDraft.Status.PUBLISHED
        draft.poll = poll
        draft.save(update_fields=['status', 'poll'])
        PollDraft.objects.filter(
            week_start=draft.week_start,
            status=PollDraft.Status.PENDING,
        ).update(status=PollDraft.Status.NOT_CHOSEN)
    return poll


def auto_publish_week(monday):
    """Publish the first confirmed pending draft if the week still has no poll."""
    if week_has_poll(monday):
        return None
    draft = (
        PollDraft.objects.filter(
            week_start=monday,
            status=PollDraft.Status.PENDING,
            verification=PollDraft.Verification.CONFIRMED,
        )
        .order_by('created_at')
        .first()
    )
    if draft is None:
        return None
    try:
        return publish_draft(draft)
    except PollPublishError as exc:
        logger.warning('Auto-publish of poll draft %s failed: %s', draft.pk, exc)
        return None


def run_weekly_poll_tasks(site_settings=None, today=None):
    """Called by run_ai_tasks. Returns a short report for the log.

    AIQuotaExceeded propagates so the command can stop.
    """
    site_settings = site_settings or config.get_site_settings()
    today = today or timezone.localdate()
    report = {'published': None, 'created': 0, 'failed': 0}
    # The main AI switch stops this too, even though publishing needs no AI call.
    if not (site_settings.enable_weekly_poll and site_settings.enable_ai and site_settings.enable_ai_poll_drafts):
        return report
    if site_settings.ai_poll_auto_publish:
        this_monday = week_start(today)
        report['published'] = auto_publish_week(this_monday)
        if report['published'] is not None:
            team_chat_bot.poll_auto_published(report['published'])
        elif (
            not week_has_poll(this_monday)
            and PollDraft.objects.filter(week_start=this_monday).exclude(status=PollDraft.Status.FAILED).exists()
        ):
            team_chat_bot.week_without_poll(this_monday)
    if today.weekday() >= GENERATION_WEEKDAY and config.ai_feature_enabled(config.POLLS, site_settings):
        monday = next_week_start(today)
        report['created'], report['failed'] = generate_drafts_for_week(monday, site_settings)
        if report['created'] or report['failed']:
            _announce_drafts(monday)
    return report


def _announce_drafts(monday):
    """Tell the team once the week's drafts are finished (all made or attempts used up)."""
    drafts = PollDraft.objects.filter(week_start=monday)
    good = drafts.exclude(status=PollDraft.Status.FAILED)
    if good.count() >= DRAFTS_PER_WEEK or drafts.count() >= MAX_ATTEMPTS_PER_WEEK:
        team_chat_bot.poll_drafts_ready(
            monday,
            good.count(),
            attach=good.filter(status=PollDraft.Status.PENDING).order_by('created_at').first(),
        )
