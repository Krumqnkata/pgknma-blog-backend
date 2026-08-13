from django.conf import settings
from django.db import migrations, models
from django.db.models import Q
import django.db.models.deletion
from django.utils import timezone


TRACKED_REFERENCES = {
    'comment': 'blog.comments',
    'poll_vote': 'blog.pollanswer',
    'meme_approved': 'blog.memeofweek',
    'song_approved': 'blog.bellsongsuggestion',
}


def _activity_row(Activity, user_id, activity, reference_id):
    try:
        reference_id = int(reference_id)
    except (TypeError, ValueError):
        return None
    if not user_id or reference_id <= 0:
        return None
    return Activity(
        user_id=user_id,
        activity=activity,
        reference_id=reference_id,
    )


def backfill_lifetime_activity(apps, schema_editor):
    Activity = apps.get_model('blog', 'LifetimeProfileActivity')
    Comment = apps.get_model('blog', 'Comments')
    PollAnswer = apps.get_model('blog', 'PollAnswer')
    Meme = apps.get_model('blog', 'MemeOfWeek')
    Song = apps.get_model('blog', 'BellSongSuggestion')
    PointTransaction = apps.get_model('blog', 'PointTransaction')

    rows = []
    now = timezone.now()
    current_sources = (
        (
            'comment',
            Comment.objects.filter(
                user_id__isnull=False,
                post__published=True,
                post__allowed=True,
            ).filter(
                Q(post__published_at__isnull=True)
                | Q(post__published_at__lte=now)
            ).values_list('user_id', 'pk'),
        ),
        (
            'poll_vote',
            PollAnswer.objects.filter(user_id__isnull=False).values_list(
                'user_id', 'pk',
            ),
        ),
        (
            'meme_approved',
            Meme.objects.filter(
                user_id__isnull=False,
                status='approved',
            ).values_list('user_id', 'pk'),
        ),
        (
            'song_approved',
            Song.objects.filter(
                user_id__isnull=False,
                status='approved',
            ).values_list('user_id', 'pk'),
        ),
    )
    for activity, queryset in current_sources:
        for user_id, reference_id in queryset.iterator(chunk_size=1000):
            row = _activity_row(Activity, user_id, activity, reference_id)
            if row:
                rows.append(row)

    # Point transactions retain references after content cleanup. They recover
    # as much historical activity as is available before this migration.
    for activity, reference_type in TRACKED_REFERENCES.items():
        transactions = PointTransaction.objects.filter(
            action=activity,
            reference_type=reference_type,
            points__gt=0,
        )
        if activity in {'meme_approved', 'song_approved'}:
            transactions = transactions.filter(reversal__isnull=True)
        for user_id, reference_id in transactions.values_list(
            'user_id', 'reference_id',
        ).iterator(chunk_size=1000):
            row = _activity_row(Activity, user_id, activity, reference_id)
            if row:
                rows.append(row)

    Activity.objects.bulk_create(
        rows,
        batch_size=1000,
        ignore_conflicts=True,
    )


class Migration(migrations.Migration):

    dependencies = [
        ('blog', '0068_notification_source_tracking'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='LifetimeProfileActivity',
            fields=[
                (
                    'id',
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name='ID',
                    ),
                ),
                (
                    'activity',
                    models.CharField(
                        choices=[
                            ('comment', 'Публикуван коментар'),
                            ('poll_vote', 'Участие в анкета'),
                            ('meme_approved', 'Одобрено меме'),
                            ('song_approved', 'Одобрена песен'),
                        ],
                        max_length=20,
                        verbose_name='Активност',
                    ),
                ),
                (
                    'reference_id',
                    models.PositiveBigIntegerField(
                        verbose_name='Идентификатор на първоначалния запис',
                    ),
                ),
                (
                    'recorded_at',
                    models.DateTimeField(
                        auto_now_add=True,
                        verbose_name='Зачетена на',
                    ),
                ),
                (
                    'user',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='lifetime_profile_activities',
                        to=settings.AUTH_USER_MODEL,
                        verbose_name='Потребител',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Пожизнена профилна активност',
                'verbose_name_plural': 'Пожизнени профилни активности',
                'ordering': ('-recorded_at', '-pk'),
            },
        ),
        migrations.AddConstraint(
            model_name='lifetimeprofileactivity',
            constraint=models.UniqueConstraint(
                fields=('user', 'activity', 'reference_id'),
                name='blog_unique_lifetime_profile_activity',
            ),
        ),
        migrations.RunPython(
            backfill_lifetime_activity,
            migrations.RunPython.noop,
        ),
    ]
