from datetime import timedelta

from django.db import migrations
from django.utils import timezone


def _monday(day):
    return day - timedelta(days=day.weekday())


def backfill_meme_weeks(apps, schema_editor):
    """Put existing approved memes into the week they were uploaded.

    Weeks that are already over are closed quietly (no announcement, no
    points), so deploying this does not send a burst of old notifications.
    The first real announcement happens when the running week ends.
    """
    MemeOfWeek = apps.get_model('blog', 'MemeOfWeek')
    MemeWeekResult = apps.get_model('blog', 'MemeWeekResult')
    GamificationPointRule = apps.get_model('blog', 'GamificationPointRule')

    today = timezone.localdate()
    current_week = _monday(today)

    for meme in MemeOfWeek.objects.filter(status='approved', week_start__isnull=True).iterator():
        uploaded = timezone.localdate(meme.created_at) if meme.created_at else today
        MemeOfWeek.objects.filter(pk=meme.pk).update(week_start=_monday(uploaded))

    past_weeks = (
        MemeOfWeek.objects.filter(status='approved', week_start__lt=current_week)
        .values_list('week_start', flat=True)
        .distinct()
    )
    for week in set(past_weeks):
        if MemeWeekResult.objects.filter(week_start=week).exists():
            continue
        memes = MemeOfWeek.objects.filter(status='approved', week_start=week).order_by('-votes', 'created_at', 'pk')
        winner = memes.first()
        MemeWeekResult.objects.create(
            week_start=week,
            winner=winner,
            winner_votes=winner.votes if winner else 0,
            meme_count=memes.count(),
        )

    GamificationPointRule.objects.get_or_create(
        action='meme_week_winner',
        defaults={'points': 50, 'daily_limit': None, 'active': True},
    )


def remove_point_rule(apps, schema_editor):
    GamificationPointRule = apps.get_model('blog', 'GamificationPointRule')
    GamificationPointRule.objects.filter(action='meme_week_winner').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('blog', '0082_meme_weeks'),
    ]

    operations = [
        migrations.RunPython(backfill_meme_weeks, remove_point_rule),
    ]
