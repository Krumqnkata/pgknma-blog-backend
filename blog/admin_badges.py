"""Counters next to sidebar items: what is waiting, visible without scrolling.

Unfold calls each function with the request. A function returns None to hide
the badge. Counts are global, so they are cached for a minute for everybody;
permissions are still checked per user on every call.
"""

from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone

CACHE_SECONDS = 60


def _cached_count(key, compute):
    value = cache.get(f'admin-badge:{key}')
    if value is None:
        value = compute()
        cache.set(f'admin-badge:{key}', value, CACHE_SECONDS)
    return value


def _badge(count):
    if not count:
        return None
    return '99+' if count > 99 else str(count)


def _user(request):
    user = getattr(request, 'user', None)
    if user is None or not user.is_active or not user.is_staff:
        return None
    return user


def posts_pending(request):
    user = _user(request)
    if user is None or not user.has_perm('blog.can_allow_posts'):
        return None
    from .models import Posts

    return _badge(_cached_count('posts', lambda: Posts.objects.filter(published=True, allowed=False).count()))


def comments_flagged(request):
    user = _user(request)
    if user is None or not user.has_perm('blog.view_comments'):
        return None
    from .models import AIContentCheck, Comments

    return _badge(_cached_count('comments', lambda: (
        Comments.objects.filter(ai_checks__verdict=AIContentCheck.VERDICT_FLAGGED).distinct().count()
    )))


def songs_pending(request):
    user = _user(request)
    if user is None or not user.has_perm('blog.change_bellsongsuggestion'):
        return None
    from .models import BellSongSuggestion

    return _badge(_cached_count('songs', lambda: BellSongSuggestion.objects.filter(status='pending').count()))


def memes_pending(request):
    user = _user(request)
    if user is None or not user.has_perm('blog.change_memeofweek'):
        return None
    from .models import MemeOfWeek

    return _badge(_cached_count('memes', lambda: MemeOfWeek.objects.filter(status='pending').count()))


def poll_drafts_pending(request):
    user = _user(request)
    if user is None or not user.has_perm('blog.view_polldraft'):
        return None
    from .ai.polls import next_week_start
    from .models import PollDraft

    return _badge(_cached_count('poll-drafts', lambda: PollDraft.objects.filter(
        week_start=next_week_start(),
        status=PollDraft.Status.PENDING,
    ).count()))


def contact_recent(request):
    user = _user(request)
    if user is None or not user.has_perm('blog.view_contactsubmission'):
        return None
    from .models import ContactSubmission

    return _badge(_cached_count('contact', lambda: ContactSubmission.objects.filter(
        submitted_at__gte=timezone.now() - timedelta(days=7),
    ).count()))


def unanswered_questions(request):
    user = _user(request)
    if user is None or not user.has_perm('blog.view_assistantunansweredquestion'):
        return None
    from .models import AssistantUnansweredQuestion

    return _badge(_cached_count('unanswered', lambda: AssistantUnansweredQuestion.objects.filter(
        status=AssistantUnansweredQuestion.STATUS_NEW,
    ).count()))
