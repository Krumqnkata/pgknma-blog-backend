from datetime import datetime, time, timedelta

from django import template
from django.contrib.auth.models import User
from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from blog.models import (
    PageView,
    PointTransaction,
    Posts,
    UserBadge,
    UserMissionProgress,
    UserProfile,
)


register = template.Library()


@register.inclusion_tag('admin/_statistics_dashboard.html', takes_context=True)
def admin_statistics_dashboard(context):
    request = context['request']
    if (
        not request.user.is_active
        or not request.user.is_staff
        or not request.user.has_perm('blog.view_pageview')
    ):
        return {'show_statistics': False}

    now = timezone.now()
    month_start = now - timedelta(days=30)
    today_start = timezone.make_aware(
        datetime.combine(timezone.localdate(), time.min),
    )

    top_posts = (
        Posts.objects.annotate(
            views_last_30_days=Count(
                'page_views',
                filter=Q(page_views__created_at__gte=month_start),
            ),
        )
        .filter(views_last_30_days__gt=0)
        .order_by('-views_last_30_days', '-published_at')[:5]
    )

    active_users = User.objects.filter(
        Q(page_views__created_at__gte=month_start)
        | Q(blog_login_sessions__last_seen_at__gte=month_start),
        is_active=True,
    ).distinct().count()

    return {
        'show_statistics': True,
        'new_users': User.objects.filter(date_joined__gte=month_start).count(),
        'active_users': active_users,
        'visits_today': PageView.objects.filter(created_at__gte=today_start).count(),
        'visits_last_30_days': PageView.objects.filter(
            created_at__gte=month_start,
        ).count(),
        'completed_missions': UserMissionProgress.objects.filter(
            completed_at__gte=month_start,
        ).count(),
        'awarded_badges': UserBadge.objects.filter(
            awarded_at__gte=month_start,
        ).count(),
        'awarded_points': PointTransaction.objects.filter(
            points__gt=0,
            created_at__gte=month_start,
        ).aggregate(total=Coalesce(Sum('points'), 0))['total'],
        'current_points': UserProfile.objects.aggregate(
            total=Coalesce(Sum('gamification_points'), 0),
        )['total'],
        'top_posts': top_posts,
    }
