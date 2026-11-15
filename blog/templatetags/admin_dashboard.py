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


@register.simple_tag(takes_context=True)
def admin_help_link(context):
    """„?“ in the top bar: opens the guide at the part about this screen."""
    from django.utils.html import format_html

    from blog.admin_help import help_url_for_path

    request = context.get('request')
    if request is None or not request.user.is_active or not request.user.is_staff:
        return ''
    url = help_url_for_path(request.path)
    if not url:
        return ''
    return format_html(
        '<a href="{}" class="ux-help-link" title="Помощ за този екран" aria-label="Помощ за този екран">'
        '<span class="material-symbols-outlined" aria-hidden="true">help</span></a>',
        url,
    )


def _attention_item(title, count, url, icon, hint):
    return {'title': title, 'count': count, 'url': url, 'icon': icon, 'hint': hint}


def _poll_overview(user):
    """This week's and next week's poll, for the dashboard card."""
    from django.urls import reverse

    from blog.ai.polls import next_week_start, week_bounds, week_start
    from blog.models import PollDraft, PollQuestion

    def poll_for(monday):
        start, end = week_bounds(monday)
        return (
            PollQuestion.objects.filter(start_date__lte=end, end_date__gte=start)
            .order_by('start_date')
            .first()
        )

    this_monday = week_start()
    next_monday = next_week_start()
    current = poll_for(this_monday)
    upcoming = poll_for(next_monday)
    drafts = PollDraft.objects.filter(week_start=next_monday, status=PollDraft.Status.PENDING)
    can_drafts = user.has_perm('blog.view_polldraft')
    drafts_url = reverse('admin:blog_polldraft_changelist') + f'?status__exact=pending&week_start={next_monday.isoformat()}'

    rows = []
    if current:
        rows.append({'label': 'Тази седмица', 'text': current.title, 'tone': 'ok',
                     'url': reverse('admin:blog_pollquestion_change', args=[current.pk])})
    else:
        rows.append({'label': 'Тази седмица', 'text': 'Няма активна анкета.', 'tone': 'warn',
                     'url': reverse('admin:blog_pollquestion_add') if user.has_perm('blog.add_pollquestion') else ''})

    if upcoming:
        rows.append({'label': 'Следващата седмица', 'text': upcoming.title, 'tone': 'ok',
                     'url': reverse('admin:blog_pollquestion_change', args=[upcoming.pk])})
    elif can_drafts and drafts.exists():
        count = drafts.count()
        rows.append({'label': 'Следващата седмица',
                     'text': f'{count} ИИ чернови чакат избор. Изберете една до неделя.',
                     'tone': 'warn', 'url': drafts_url})
    else:
        rows.append({'label': 'Следващата седмица',
                     'text': 'Още няма анкета. ИИ черновите се появяват в четвъртък (ако са включени в настройките).',
                     'tone': 'info', 'url': ''})
    return rows


@register.inclusion_tag('admin/_attention_dashboard.html', takes_context=True)
def admin_attention_dashboard(context):
    """„Какво чака теб“: only the things the current user can act on, with counts."""
    from django.urls import reverse

    from blog.models import (
        AIContentCheck,
        AssistantUnansweredQuestion,
        BellSongSuggestion,
        Comments,
        Event,
        MemeOfWeek,
    )

    request = context['request']
    user = request.user
    if not user.is_active or not user.is_staff:
        return {'show_attention': False}

    items = []
    if user.has_perm('blog.can_allow_posts'):
        count = Posts.objects.filter(published=True, allowed=False).count()
        items.append(_attention_item(
            'Публикации за одобрение', count,
            reverse('admin:blog_posts_changelist') + '?published__exact=1&allowed__exact=0',
            'article', 'Написани са, но още не се виждат на сайта.',
        ))
    if user.has_perm('blog.change_bellsongsuggestion'):
        count = BellSongSuggestion.objects.filter(status='pending').count()
        items.append(_attention_item(
            'Песни за звънеца', count,
            reverse('admin:blog_bellsongsuggestion_changelist') + '?status__exact=pending',
            'music_note', 'Предложения, които чакат одобрение.',
        ))
    if user.has_perm('blog.change_memeofweek'):
        count = MemeOfWeek.objects.filter(status='pending').count()
        items.append(_attention_item(
            'Мемета', count,
            reverse('admin:blog_memeofweek_changelist') + '?status__exact=pending',
            'sentiment_very_satisfied', 'Качени мемета, които чакат преглед.',
        ))
    if user.has_perm('blog.view_comments'):
        count = Comments.objects.filter(ai_checks__verdict=AIContentCheck.VERDICT_FLAGGED).distinct().count()
        items.append(_attention_item(
            'Коментари, маркирани от ИИ', count,
            reverse('admin:blog_comments_changelist') + '?ai_check=flagged',
            'comment', 'Може да са неподходящи. Нищо не е изтрито автоматично.',
        ))
    if user.has_perm('blog.view_assistantunansweredquestion'):
        count = AssistantUnansweredQuestion.objects.filter(status=AssistantUnansweredQuestion.STATUS_NEW).count()
        items.append(_attention_item(
            'Въпроси без отговор (ИИ асистент)', count,
            reverse('admin:blog_assistantunansweredquestion_changelist') + '?status__exact=new',
            'contact_support', 'Добавете правило, за да може асистентът да отговаря.',
        ))

    if user.has_perm('blog.view_contactsubmission'):
        from blog.models import ContactSubmission

        count = ContactSubmission.objects.filter(submitted_at__gte=timezone.now() - timedelta(days=7)).count()
        items.append(_attention_item(
            'Съобщения от формата за контакт (7 дни)', count,
            reverse('admin:blog_contactsubmission_changelist'),
            'mail', 'Изпратени през сайта през последната седмица.',
        ))

    pending = [item for item in items if item['count']]

    upcoming_events = []
    if user.has_perm('blog.view_event'):
        now = timezone.now()
        upcoming_events = list(
            Event.objects.filter(start_datetime__gte=now, start_datetime__lte=now + timedelta(days=7))
            .order_by('start_datetime')[:5]
        )

    return {
        'show_attention': True,
        'pending_items': pending,
        'has_checks': bool(items),
        'poll_rows': _poll_overview(user) if user.has_perm('blog.view_pollquestion') else [],
        'upcoming_events': upcoming_events,
        'can_change_events': user.has_perm('blog.change_event'),
        'my_drafts': _my_drafts(user),
        'system_rows': _system_status() if user.has_perm('blog.change_sitesettings') else [],
    }


def _my_drafts(user):
    """The user's own unfinished posts: drafts and posts waiting for approval."""
    from django.urls import reverse

    if not user.has_perm('blog.add_posts'):
        return []
    posts = (
        Posts.objects.filter(author=user)
        .filter(Q(published=False) | Q(published=True, allowed=False))
        .order_by('-created_at')[:5]
    )
    return [
        {
            'title': post.title,
            'state': 'Чернова' if not post.published else 'Чака одобрение',
            'tone': 'muted' if not post.published else 'warn',
            'url': reverse('admin:blog_posts_change', args=[post.pk]),
        }
        for post in posts
    ]


def _ago(moment, now):
    minutes = int((now - moment).total_seconds() // 60)
    if minutes < 1:
        return 'току-що'
    if minutes < 60:
        return f'преди {minutes} мин.'
    if minutes < 48 * 60:
        return f'преди {minutes // 60} ч.'
    return timezone.localtime(moment).strftime('%d.%m.%Y %H:%M')


def _timer_row(label, last_run, now):
    if last_run is None:
        return {'label': label, 'text': 'Още не е пускан (или е от преди обновлението).', 'tone': 'warn'}
    late = now - last_run > timedelta(minutes=30)
    text = f'Последно пускане: {_ago(last_run, now)}'
    if late:
        text += ' Таймерът на сървъра може да е спрял.'
    return {'label': label, 'text': text, 'tone': 'warn' if late else 'ok'}


def _system_status():
    """Short health card for people who manage the site settings."""
    from blog.ai.client import credentials_configured
    from blog.models import SiteSettings

    site_settings, _ = SiteSettings.objects.get_or_create(pk=1)
    now = timezone.now()
    rows = []
    if site_settings.maintenance_mode:
        rows.append({'label': 'Сайт', 'text': 'Режим на поддръжка: посетителите не виждат сайта.', 'tone': 'warn'})
    else:
        rows.append({'label': 'Сайт', 'text': 'Работи нормално.', 'tone': 'ok'})

    if not site_settings.enable_ai:
        rows.append({'label': 'ИИ', 'text': 'Изключен от общото копче в настройките.', 'tone': 'info'})
    elif not credentials_configured():
        rows.append({'label': 'ИИ', 'text': 'Включен, но липсват ключовете за Cloudflare на сървъра.', 'tone': 'warn'})
    else:
        rows.append({'label': 'ИИ', 'text': 'Включен, ключовете за Cloudflare са настроени.', 'tone': 'ok'})

    rows.append(_timer_row('Таймер за ИИ задачи', site_settings.ai_tasks_last_run_at, now))
    rows.append(_timer_row('Таймер за известия', site_settings.notifications_last_run_at, now))

    if not site_settings.enable_user_registration:
        rows.append({'label': 'Регистрации', 'text': 'Спрени: нови потребители не могат да се регистрират.', 'tone': 'info'})
    return rows


@register.simple_tag(takes_context=True)
def admin_page_title(context):
    """Browser-tab title: „Публикации“, „Нова публикация“, „Публикация“.

    Used by templates/admin/base_site.html; other pages keep their own title.
    """
    from blog.admin_titles import add_label, item_title, list_title

    title = context.get('title') or ''
    if context.get('is_popup'):
        return title  # „Изберете …“ is right in a picker window
    cl = context.get('cl')
    if cl is not None and getattr(cl, 'opts', None) is not None:
        return list_title(cl.opts)
    opts = context.get('opts')
    if opts is not None and 'adminform' in context:
        if context.get('add'):
            return add_label(opts)
        return item_title(opts)
    return title


@register.filter
def admin_add_label(opts):
    """„Нова публикация“ for the add buttons."""
    from blog.admin_titles import add_label

    return add_label(opts)


@register.filter
def admin_delete_label(opts):
    """„Изтрий публикация“ for the delete button on a form."""
    from blog.admin_titles import lower_noun

    return f'Изтрий {lower_noun(opts.verbose_name)}'
