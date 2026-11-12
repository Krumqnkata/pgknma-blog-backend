"""Querysets and search filters for content that anyone may see.

Shared by the global search and the AI assistant, so both see exactly the
same public articles and events.
"""

from django.db.models import Q
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import Event, Posts


def public_posts_queryset():
    now = timezone.now()
    return (
        Posts.objects.filter(published=True, allowed=True)
        .filter(Q(published_at__isnull=True) | Q(published_at__lte=now))
        .annotate(visible_at=Coalesce('published_at', 'created_at'))
        .order_by('-visible_at')
    )


def public_events_queryset():
    return Event.objects.filter(published=True)


def post_search_q(term):
    return (
        Q(title__icontains=term)
        | Q(hook__icontains=term)
        | Q(content__icontains=term)
        | Q(categories__full_name__icontains=term)
    )


def event_search_q(term):
    return (
        Q(title__icontains=term)
        | Q(description__icontains=term)
        | Q(location__icontains=term)
        | Q(category__icontains=term)
        | Q(attendees_text__icontains=term)
    )
