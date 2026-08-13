from django.db.models import Count

from .models import LifetimeProfileActivity


LIFETIME_ACTIVITY_KEYS = tuple(
    value for value, _label in LifetimeProfileActivity.Activity.choices
)


def record_lifetime_activity(user_id, activity, reference_id):
    """Record one durable profile activity without counting it twice."""
    if not user_id or not reference_id or activity not in LIFETIME_ACTIVITY_KEYS:
        return None, False
    return LifetimeProfileActivity.objects.get_or_create(
        user_id=user_id,
        activity=activity,
        reference_id=reference_id,
    )


def remove_lifetime_activity(user_id, activity, reference_id):
    """Remove an approval that was explicitly revoked, not archived."""
    if not user_id or not reference_id:
        return 0
    deleted, _details = LifetimeProfileActivity.objects.filter(
        user_id=user_id,
        activity=activity,
        reference_id=reference_id,
    ).delete()
    return deleted


def lifetime_activity_counts(user_id):
    counts = {key: 0 for key in LIFETIME_ACTIVITY_KEYS}
    rows = (
        LifetimeProfileActivity.objects.filter(user_id=user_id)
        .values('activity')
        .annotate(total=Count('pk'))
    )
    counts.update({row['activity']: row['total'] for row in rows})
    return counts
