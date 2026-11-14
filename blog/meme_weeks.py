"""Weekly cycle of „Меме на седмицата“.

A week runs Monday 00:00 – Sunday 23:59 (school time, settings.TIME_ZONE).
A meme joins the week in which it is approved (MemeOfWeek.week_start) and
can only be voted for during that week. After the week ends, the meme with
the most votes wins: the winner is announced to everyone and its author gets
bonus points (gamification action ``meme_week_winner``, editable in the admin).
"""

import logging
from datetime import datetime, time, timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from .automatic_notifications import create_notification_once
from .models import MemeOfWeek, MemeWeekResult, SiteSettings, meme_week_start
from .notification_categories import CONTENT_MEME

logger = logging.getLogger(__name__)

WINNER_ACTION = 'meme_week_winner'


def current_week_start(now=None):
    return meme_week_start(timezone.localdate(now or timezone.now()))


def week_end(week_start):
    """Last moment of the week as an aware datetime (Sunday 23:59:59.999999)."""
    next_monday = timezone.make_aware(datetime.combine(week_start + timedelta(days=7), time.min))
    return next_monday - timedelta(microseconds=1)


def approved_memes(week_start):
    return MemeOfWeek.objects.filter(status='approved', week_start=week_start)


def ranked(queryset):
    """Most votes first; on a tie the meme uploaded earlier wins."""
    return queryset.order_by('-votes', 'created_at', 'pk')


def latest_winner_result():
    """The most recent closed week that has a still-approved winner."""
    return (
        MemeWeekResult.objects.filter(winner__isnull=False, winner__status='approved')
        .select_related('winner', 'winner__user')
        .order_by('-week_start')
        .first()
    )


def _feature_enabled():
    site_settings = SiteSettings.objects.filter(pk=1).first()
    return site_settings is None or site_settings.enable_meme_of_the_week


def _award_points(result):
    from .gamification.services import award_tracked_activity

    winner = result.winner
    if not winner or not winner.user_id or not winner.user.is_active:
        return
    award_tracked_activity(winner.user, WINNER_ACTION, result._meta.label_lower, result.pk)
    MemeWeekResult.objects.filter(pk=result.pk).update(points_awarded_at=timezone.now())


def _announce(result):
    winner = result.winner
    author = winner.user.username if winner.user_id and winner.user else 'анонимен автор'
    votes = result.winner_votes
    votes_text = '1 глас' if votes == 1 else f'{votes} гласа'
    create_notification_once(
        key=f'meme-week:{result.week_start.isoformat()}',
        text=f'Меме на седмицата: „{winner.title}“ от {author} с {votes_text}!',
        category=CONTENT_MEME,
        action_url='/meme-of-the-week',
        source=winner,
    )
    MemeWeekResult.objects.filter(pk=result.pk).update(announced_at=timezone.now())


def finalize_week(week_start, *, announce=True):
    """Close one finished week. Returns the result, or None if it was already closed."""
    memes = ranked(approved_memes(week_start))
    winner = memes.first()
    try:
        with transaction.atomic():
            result = MemeWeekResult.objects.create(
                week_start=week_start,
                winner=winner,
                winner_votes=winner.votes if winner else 0,
                meme_count=memes.count(),
            )
    except IntegrityError:
        return None  # Another run closed this week first.

    if announce and winner:
        try:
            _award_points(result)
            _announce(result)
        except Exception:  # The result stays; a missing bonus must not block other weeks.
            logger.exception('Could not reward or announce meme week %s', week_start)
    return result


def finalize_meme_weeks(now=None):
    """Close every finished week that has no result yet.

    Only the week that has just ended is announced and rewarded. Older weeks
    (for example after the server was down) are closed quietly, so nobody
    gets a pile of late notifications.
    """
    if not _feature_enabled():
        return []
    current = current_week_start(now)
    previous = current - timedelta(days=7)
    closed = set(MemeWeekResult.objects.values_list('week_start', flat=True))
    weeks = sorted(
        set(
            MemeOfWeek.objects.filter(status='approved', week_start__lt=current)
            .values_list('week_start', flat=True)
        ) - closed
    )
    results = []
    for week in weeks:
        result = finalize_week(week, announce=week >= previous)
        if result is not None:
            results.append(result)
    return results
