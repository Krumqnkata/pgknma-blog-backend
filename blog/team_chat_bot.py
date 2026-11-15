"""Automatic messages from the site to the staff team chat.

Every kind of message has its own switch in „Настройки на сайта“. Messages
never repeat personal data or the text of what was written: the record is
attached as a shared card, and each staff member sees that card only if
their own admin permissions allow it.
"""

import logging
from datetime import datetime, time, timedelta

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

BOT_NAME = 'Сайтът (автоматично)'
DIGEST_HOUR = 8  # local time; the daily summary is posted after this hour

POLLS = 'team_chat_notify_polls'
PENDING_DIGEST = 'team_chat_notify_pending_digest'
AI_FLAGS = 'team_chat_notify_ai_flags'
CONTACT = 'team_chat_notify_contact'


def _enabled(switch):
    from .models import SiteSettings

    site_settings = SiteSettings.objects.filter(pk=1).first()
    if site_settings is None:
        return SiteSettings._meta.get_field(switch).default
    return bool(getattr(site_settings, switch))


def _already_posted(text, since):
    from .models import TeamChatMessage

    return TeamChatMessage.objects.filter(
        author__isnull=True,
        author_name=BOT_NAME,
        discussion__isnull=True,
        text=text,
        created_at__gte=since,
    ).exists()


def post(text, *, switch, attach=None, once_since=None):
    """Post to the main team chat. Returns the message or None.

    `once_since`: skip if the same text was already posted after that moment.
    Errors are logged and never break the caller (a page save or a timer).
    """
    from .models import TeamChatMessage, TeamChatSharedObject

    try:
        if not _enabled(switch):
            return None
        text = text.strip()[:4000]
        if once_since is not None and _already_posted(text, once_since):
            return None
        with transaction.atomic():
            message = TeamChatMessage.objects.create(author=None, author_name=BOT_NAME, text=text)
            if attach is not None:
                TeamChatSharedObject.objects.create(
                    message=message,
                    content_type=ContentType.objects.get_for_model(attach, for_concrete_model=False),
                    object_id=str(attach.pk),
                )
        return message
    except Exception:  # noqa: BLE001 - a chat message must never break the real work
        logger.exception('Could not post an automatic team chat message.')
        return None


def _week_label(monday):
    sunday = monday + timedelta(days=6)
    return f'{monday:%d.%m} – {sunday:%d.%m.%Y}'


def _local_midnight(day):
    return timezone.make_aware(datetime.combine(day, time.min))


# --- Weekly poll -----------------------------------------------------------

def poll_drafts_ready(monday, good_count, attach=None):
    if good_count:
        text = (
            f'🗳️ ИИ подготви {good_count} чернови за анкетата за седмицата {_week_label(monday)}. '
            'Изберете една от „Седмична анкета → ИИ чернови за анкети“.'
        )
    else:
        text = (
            f'🗳️ ИИ не успя да подготви чернови за анкетата за седмицата {_week_label(monday)}. '
            'Добавете анкета ръчно от „Седмична анкета → Въпроси за анкети“.'
        )
    return post(text, switch=POLLS, attach=attach, once_since=_local_midnight(monday - timedelta(days=7)))


def poll_auto_published(poll):
    text = '🗳️ Анкетата за тази седмица е пусната автоматично (потвърдена ИИ чернова).'
    return post(text, switch=POLLS, attach=poll)


def week_without_poll(monday):
    text = (
        f'⚠️ Седмицата {_week_label(monday)} е без анкета: няма избрана чернова, '
        'а ИИ не потвърди верния отговор на нито една. Можете да изберете чернова ръчно.'
    )
    return post(text, switch=POLLS, once_since=_local_midnight(monday))


# --- Moderation ------------------------------------------------------------

FLAGGED_LABELS = {
    'comments': 'коментар',
    'bellsongsuggestion': 'предложение за песен',
    'memeofweek': 'меме',
}


def ai_flagged(obj, check):
    label = FLAGGED_LABELS.get(obj._meta.model_name, str(obj._meta.verbose_name).lower())
    category = check.get_category_display() or 'без категория'
    text = f'⚠️ ИИ маркира {label} като възможно неподходящо ({category}). Нищо не е скрито автоматично.'
    return post(text, switch=AI_FLAGS, attach=obj)


def contact_received(submission):
    text = f'✉️ Ново съобщение от формата за контакт ({submission.get_reason_display()}).'
    return post(text, switch=CONTACT, attach=submission)


def pending_counts():
    """Things waiting for a person, as (label, count) pairs."""
    from .models import (
        AIContentCheck,
        AssistantUnansweredQuestion,
        BellSongSuggestion,
        Comments,
        MemeOfWeek,
        Posts,
    )

    return [
        ('статии за одобрение', Posts.objects.filter(published=True, allowed=False).count()),
        ('песни за звънеца', BellSongSuggestion.objects.filter(status='pending').count()),
        ('мемета', MemeOfWeek.objects.filter(status='pending').count()),
        (
            'коментари, маркирани от ИИ',
            Comments.objects.filter(ai_checks__verdict=AIContentCheck.VERDICT_FLAGGED).distinct().count(),
        ),
        (
            'въпроси без отговор към ИИ асистента',
            AssistantUnansweredQuestion.objects.filter(status=AssistantUnansweredQuestion.STATUS_NEW).count(),
        ),
    ]


def daily_pending_digest(now=None):
    """Once a day after DIGEST_HOUR: what is waiting. Nothing if all is reviewed."""
    now = timezone.localtime(now or timezone.now())
    if now.hour < DIGEST_HOUR:
        return None
    parts = [f'{label}: {count}' for label, count in pending_counts() if count]
    if not parts:
        return None
    # The date in the text makes the message unique per day.
    marker = f'📋 Чака преглед ({now:%d.%m.%Y}): '
    if _already_posted_prefix(marker):
        return None
    text = marker + ', '.join(parts) + '. Всичко е в „Какво чака теб“ на началната страница.'
    return post(text, switch=PENDING_DIGEST)


def _already_posted_prefix(prefix):
    from .models import TeamChatMessage

    return TeamChatMessage.objects.filter(
        author__isnull=True,
        author_name=BOT_NAME,
        discussion__isnull=True,
        text__startswith=prefix,
    ).exists()
