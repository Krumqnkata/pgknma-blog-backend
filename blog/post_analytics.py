from datetime import datetime

from django.db.models import Count, IntegerField, OuterRef, QuerySet, Subquery, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import PageView


def current_site_month_bounds(at: datetime | None = None) -> tuple[datetime, datetime]:
    """Return the current calendar month in the site's configured time zone."""
    site_timezone = timezone.get_default_timezone()
    local_now = timezone.localtime(at or timezone.now(), site_timezone)
    start = local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end


def with_current_month_views(queryset: QuerySet, *, at: datetime | None = None) -> QuerySet:
    """Annotate posts with views from the current Sofia calendar month."""
    start, end = current_site_month_bounds(at)
    monthly_views = (
        PageView.objects.filter(
            post_id=OuterRef('pk'),
            created_at__gte=start,
            created_at__lt=end,
        )
        .values('post_id')
        .annotate(total=Count('pk'))
        .values('total')[:1]
    )
    return queryset.annotate(
        views_this_month=Coalesce(
            Subquery(monthly_views, output_field=IntegerField()),
            Value(0),
        )
    )
