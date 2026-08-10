from dataclasses import dataclass
from datetime import datetime, time, timedelta

from django.db import models, transaction
from django.utils import timezone

from blog.models import (
    Badge,
    GamificationLevel,
    Mission,
    MissionActivity,
    Notification,
    PointTransaction,
    UserBadge,
    UserDigitalReward,
    UserMissionProgress,
    UserProfile,
)

from .config import ACTION_DESCRIPTIONS, daily_limit, point_value


@dataclass(frozen=True)
class LevelState:
    current: GamificationLevel | None
    next: GamificationLevel | None
    progress_percent: int
    points_to_next: int


def level_state(points):
    levels = list(GamificationLevel.objects.order_by('minimum_points'))
    if not levels:
        return LevelState(None, None, 100, 0)

    current = levels[0]
    next_level = None
    for index, level in enumerate(levels):
        if points >= level.minimum_points:
            current = level
            next_level = levels[index + 1] if index + 1 < len(levels) else None
        else:
            next_level = level
            break

    if next_level is None:
        return LevelState(current, None, 100, 0)

    return LevelState(
        current,
        next_level,
        min(100, round(points / max(next_level.minimum_points, 1) * 100)),
        max(next_level.minimum_points - points, 0),
    )


def _notification(
    user,
    text,
    category,
    action_url='/profile?tab=progress',
):
    Notification.objects.create(
        user=user,
        text=text,
        category=category,
        action_url=action_url,
    )


def change_points(
    *,
    user,
    points,
    action,
    description,
    idempotency_key=None,
    reference_type='',
    reference_id='',
    source=PointTransaction.Source.ACTIVITY,
    created_by=None,
    reversal_of=None,
    metadata=None,
    evaluate_badges=True,
):
    """Apply one transaction safely and return (transaction, created)."""
    requested_points = int(points)
    if requested_points == 0:
        return None, False

    with transaction.atomic():
        UserProfile.objects.get_or_create(user=user)
        profile = UserProfile.objects.select_for_update().get(user=user)

        if idempotency_key:
            existing = PointTransaction.objects.filter(
                idempotency_key=idempotency_key
            ).first()
            if existing:
                return existing, False

        old_level = level_state(profile.gamification_points).current
        new_balance = max(0, profile.gamification_points + requested_points)
        applied_points = new_balance - profile.gamification_points
        if applied_points == 0:
            return None, False

        point_transaction = PointTransaction.objects.create(
            user=user,
            action=action,
            points=applied_points,
            balance_after=new_balance,
            description=description,
            source=source,
            reference_type=reference_type,
            reference_id=str(reference_id) if reference_id != '' else '',
            idempotency_key=idempotency_key,
            created_by=created_by,
            reversal_of=reversal_of,
            metadata=metadata or {},
        )
        profile.gamification_points = new_balance
        profile.save(update_fields=('gamification_points',))

        new_level = level_state(new_balance).current
        if applied_points > 0:
            notification_url = (
                f'/post/{reference_id}'
                if reference_type == 'blog.posts' and reference_id
                else '/profile?tab=progress'
            )
            _notification(
                user,
                f'⭐ +{applied_points} точки — {description}.',
                'gamification_points',
                notification_url,
            )
        if old_level and new_level and old_level.pk != new_level.pk:
            _notification(
                user,
                f'🎉 Ново ниво: {new_level.title}!',
                'gamification_level',
            )

    if evaluate_badges and applied_points > 0:
        evaluate_automatic_badges(user, action)
    return point_transaction, True


def award_tracked_activity(user, action, reference_type, reference_id):
    if not user or not user.is_authenticated:
        return None, False

    base_key = f'activity:{action}:{reference_type}:{reference_id}'
    prior_awards = PointTransaction.objects.filter(
        user=user,
        action=action,
        reference_type=reference_type,
        reference_id=str(reference_id),
        points__gt=0,
    )
    active_award = prior_awards.filter(reversal__isnull=True).first()
    if active_award:
        return active_award, False
    award_number = prior_awards.count() + 1
    idempotency_key = base_key if award_number == 1 else f'{base_key}:cycle:{award_number}'

    limit = daily_limit(action)
    if limit == 0:
        return None, False
    if limit is not None:
        local_day = timezone.localdate()
        day_start = timezone.make_aware(datetime.combine(local_day, time.min))
        if PointTransaction.objects.filter(
            user=user,
            action=action,
            points__gt=0,
            created_at__gte=day_start,
            reversal__isnull=True,
        ).count() >= limit:
            return None, False

    points = point_value(action)
    if points <= 0:
        return None, False
    return change_points(
        user=user,
        points=points,
        action=action,
        description=ACTION_DESCRIPTIONS[action],
        idempotency_key=idempotency_key,
        reference_type=reference_type,
        reference_id=reference_id,
    )


def award_activity(user, action, reference):
    return award_tracked_activity(
        user,
        action,
        reference._meta.label_lower,
        reference.pk,
    )


def reverse_activity(reference, action, description=None):
    reference_type = reference._meta.label_lower
    original = PointTransaction.objects.filter(
        action=action,
        reference_type=reference_type,
        reference_id=str(reference.pk),
        points__gt=0,
        reversal__isnull=True,
    ).select_related('user').order_by('-created_at', '-pk').first()
    if not original:
        return None, False
    return reverse_transaction(original, description=description)


def reverse_transaction(original, description=None, created_by=None):
    if hasattr(original, 'reversal'):
        return original.reversal, False
    return change_points(
        user=original.user,
        points=-original.points,
        action=f'{original.action}_reversed',
        description=description or f'Корекция: {original.description}',
        idempotency_key=f'reversal:{original.pk}',
        reference_type=original.reference_type,
        reference_id=original.reference_id,
        source=PointTransaction.Source.REVERSAL,
        created_by=created_by,
        reversal_of=original,
        evaluate_badges=False,
    )


def award_badge(user, badge, *, awarded_by=None, reason='', award_key='default'):
    now = timezone.now()
    if not badge.active or (badge.starts_at and badge.starts_at > now):
        return None, False
    if badge.ends_at and badge.ends_at < now:
        return None, False
    if not badge.repeatable:
        existing = UserBadge.objects.filter(user=user, badge=badge).first()
        if existing:
            sync_badge_rewards(badge, user=user)
            return existing, False

    with transaction.atomic():
        user_badge, created = UserBadge.objects.get_or_create(
            user=user,
            badge=badge,
            award_key=award_key,
            defaults={'awarded_by': awarded_by, 'reason': reason},
        )
        if not created:
            return user_badge, False
        _notification(
            user,
            f'🏆 Нова значка: „{badge.title}“!',
            'gamification_badge',
        )
        if badge.points_reward:
            change_points(
                user=user,
                points=badge.points_reward,
                action='badge_reward',
                description=f'Бонус от значка „{badge.title}“',
                idempotency_key=f'badge-reward:{user.pk}:{user_badge.pk}',
                reference_type=UserBadge._meta.label_lower,
                reference_id=user_badge.pk,
                source=PointTransaction.Source.BADGE,
                evaluate_badges=False,
            )
        sync_badge_rewards(badge, user=user)
    return user_badge, True


def unlock_digital_reward(user, reward, *, source_badge=None):
    row, created = UserDigitalReward.objects.get_or_create(
        user=user,
        reward=reward,
        defaults={'source_badge': source_badge},
    )
    if created:
        _notification(
            user,
            f'🎁 Нова дигитална награда: „{reward.title}“!',
            'gamification_reward',
        )
    return row, created


def sync_badge_rewards(badge, *, user=None):
    rewards = list(badge.digital_rewards.filter(active=True))
    if not rewards:
        return 0
    if user is not None:
        users = [user]
    else:
        users = [row.user for row in badge.awards.select_related('user')]
    created = 0
    for badge_user in users:
        for reward in rewards:
            _, was_created = unlock_digital_reward(
                badge_user,
                reward,
                source_badge=badge,
            )
            created += int(was_created)
    return created


def select_digital_reward(user, reward_key, *, selected=True):
    with transaction.atomic():
        owned = (
            UserDigitalReward.objects.select_for_update()
            .select_related('reward')
            .get(user=user, reward__key=reward_key, reward__active=True)
        )
        same_kind = UserDigitalReward.objects.select_for_update().filter(
            user=user,
            reward__kind=owned.reward.kind,
        )
        same_kind.exclude(pk=owned.pk).update(selected=False)
        if owned.selected != bool(selected):
            owned.selected = bool(selected)
            owned.save(update_fields=('selected',))
    return owned


def _badge_metric(user, requirement):
    requirement_type = requirement.get('type')
    if not requirement_type:
        if 'action' in requirement:
            requirement_type = 'action_count'
        elif 'activity_days' in requirement:
            requirement_type = 'active_days'
        elif requirement.get('manual_or_event'):
            requirement_type = 'manual'

    if requirement_type == 'action_count':
        action = str(requirement.get('action', '')).strip()
        if not action:
            return 0, 1, requirement_type
        target = max(int(requirement.get('target', requirement.get('count', 1))), 1)
        value = PointTransaction.objects.filter(
            user=user,
            action=action,
            points__gt=0,
            reversal__isnull=True,
        ).count()
        return value, target, requirement_type
    if requirement_type == 'active_days':
        target = max(int(requirement.get('target', requirement.get('activity_days', 1))), 1)
        value = PointTransaction.objects.filter(
            user=user,
            points__gt=0,
            reversal__isnull=True,
        ).dates('created_at', 'day').count()
        return value, target, requirement_type
    if requirement_type == 'total_points':
        target = max(int(requirement.get('target', 1)), 1)
        profile, _ = UserProfile.objects.get_or_create(user=user)
        return profile.gamification_points, target, requirement_type
    return 0, 1, 'manual'


def evaluate_badge(user, badge, *, triggering_action=None):
    requirement = badge.requirement or {}
    requirement_action = str(requirement.get('action', '')).strip()
    if triggering_action and requirement_action and requirement_action != triggering_action:
        return None, False
    value, target, requirement_type = _badge_metric(user, requirement)
    if requirement_type == 'manual' or value < target:
        return None, False
    award_key = 'default'
    if badge.repeatable:
        award_key = f'cycle-{max(value // target, 1)}'
    return award_badge(
        user,
        badge,
        reason='Автоматично изпълнено условие',
        award_key=award_key,
    )


def evaluate_automatic_badges(user, action=None):
    now = timezone.now()
    badges = Badge.objects.filter(active=True).filter(
        models.Q(starts_at__isnull=True) | models.Q(starts_at__lte=now),
        models.Q(ends_at__isnull=True) | models.Q(ends_at__gte=now),
    )
    awarded = 0
    for badge in badges:
        _, created = evaluate_badge(user, badge, triggering_action=action)
        awarded += int(created)
    return awarded


def evaluate_badge_for_all_users(badge):
    awarded = 0
    for profile in UserProfile.objects.select_related('user').filter(user__is_active=True):
        _, created = evaluate_badge(profile.user, badge)
        awarded += int(created)
    return awarded


def ensure_current_weekly_mission():
    today = timezone.localdate()
    monday = today - timedelta(days=today.weekday())
    start = timezone.make_aware(datetime.combine(monday, time.min))
    end = start + timedelta(days=7)
    iso_year, iso_week, _ = monday.isocalendar()
    mission, _ = Mission.objects.get_or_create(
        slug=f'active-week-{iso_year}-{iso_week:02d}',
        defaults={
            'title': 'Активна седмица',
            'description': 'Опознайте блога и участвайте в училищната общност.',
            'start_date': start,
            'end_date': end,
            'reward_points': 50,
            'active': True,
            'requirements': [
                {
                    'key': 'read_posts',
                    'action': 'post_read',
                    'label': 'Прочети 3 различни публикации',
                    'target': 3,
                },
                {
                    'key': 'poll_vote',
                    'action': 'poll_vote',
                    'label': 'Гласувай в 1 анкета',
                    'target': 1,
                },
                {
                    'key': 'calendar_visit',
                    'action': 'calendar_visit',
                    'label': 'Посети училищния календар',
                    'target': 1,
                },
                {
                    'key': 'comment',
                    'action': 'comment',
                    'label': 'Остави 1 валиден коментар',
                    'target': 1,
                },
            ],
        },
    )
    return mission


def _mission_progress_payload(progress_row):
    mission = progress_row.mission
    requirements = []
    completed_goals = 0
    for requirement in mission.requirements:
        key = str(requirement.get('key', requirement.get('action', 'goal')))
        target = max(int(requirement.get('target', 1)), 1)
        current = min(int(progress_row.progress.get(key, 0)), target)
        complete = current >= target
        completed_goals += int(complete)
        requirements.append({
            'key': key,
            'label': str(requirement.get('label', key)),
            'current': current,
            'target': target,
            'complete': complete,
        })
    percent = (
        round(completed_goals / len(requirements) * 100)
        if requirements else 100
    )
    return {
        'slug': mission.slug,
        'title': mission.title,
        'description': mission.description,
        'start_date': mission.start_date,
        'end_date': mission.end_date,
        'reward_points': mission.reward_points,
        'requirements': requirements,
        'progress_percent': percent,
        'completed_at': progress_row.completed_at,
        'rewarded_at': progress_row.rewarded_at,
    }


def active_missions_for_user(user):
    ensure_current_weekly_mission()
    now = timezone.now()
    missions = Mission.objects.filter(
        active=True,
        start_date__lte=now,
        end_date__gt=now,
    ).order_by('end_date')
    payload = []
    for mission in missions:
        row, _ = UserMissionProgress.objects.get_or_create(
            user=user,
            mission=mission,
        )
        payload.append(_mission_progress_payload(row))
    return payload


def record_mission_activity(user, action, reference_id):
    """Record a privacy-light unique activity and update all active missions."""
    ensure_current_weekly_mission()
    now = timezone.now()
    completed = []
    for mission in Mission.objects.filter(
        active=True,
        start_date__lte=now,
        end_date__gt=now,
    ):
        accepted_actions = {
            str(item.get('action')) for item in mission.requirements
        }
        if action not in accepted_actions:
            continue
        with transaction.atomic():
            _, created = MissionActivity.objects.get_or_create(
                user=user,
                mission=mission,
                action=action,
                reference_id=str(reference_id),
            )
            if not created:
                continue
            UserMissionProgress.objects.get_or_create(user=user, mission=mission)
            row = UserMissionProgress.objects.select_for_update().get(
                user=user,
                mission=mission,
            )
            progress = {}
            all_complete = True
            for requirement in mission.requirements:
                requirement_action = str(requirement.get('action'))
                key = str(requirement.get('key', requirement_action))
                target = max(int(requirement.get('target', 1)), 1)
                current = MissionActivity.objects.filter(
                    user=user,
                    mission=mission,
                    action=requirement_action,
                ).count()
                progress[key] = min(current, target)
                all_complete = all_complete and current >= target
            row.progress = progress
            if all_complete and row.completed_at is None:
                row.completed_at = now
            should_reward = all_complete and row.rewarded_at is None
            if should_reward:
                row.rewarded_at = now
            row.save(
                update_fields=('progress', 'completed_at', 'rewarded_at', 'updated_at')
            )
            if should_reward and mission.reward_points:
                prior_rewards = PointTransaction.objects.filter(
                    user=user,
                    action='mission_completed',
                    reference_type=Mission._meta.label_lower,
                    reference_id=str(mission.pk),
                    points__gt=0,
                )
                reward_number = prior_rewards.count() + 1
                base_key = f'mission-reward:{user.pk}:{mission.pk}'
                reward_key = (
                    base_key if reward_number == 1
                    else f'{base_key}:cycle:{reward_number}'
                )
                change_points(
                    user=user,
                    points=mission.reward_points,
                    action='mission_completed',
                    description=f'Завършена мисия „{mission.title}“',
                    idempotency_key=reward_key,
                    reference_type=Mission._meta.label_lower,
                    reference_id=mission.pk,
                    evaluate_badges=False,
                )
                _notification(
                    user,
                    f'🎯 Завършихте мисията „{mission.title}“!',
                    'gamification_mission',
                )
                completed.append(mission.pk)
    return completed


def remove_mission_activity(user, action, reference_id):
    activities = MissionActivity.objects.filter(
        user=user,
        action=action,
        reference_id=str(reference_id),
    ).select_related('mission')
    for activity in list(activities):
        mission = activity.mission
        with transaction.atomic():
            activity.delete()
            row = UserMissionProgress.objects.select_for_update().filter(
                user=user,
                mission=mission,
            ).first()
            if not row:
                continue
            progress = {}
            all_complete = True
            for requirement in mission.requirements:
                requirement_action = str(requirement.get('action'))
                key = str(requirement.get('key', requirement_action))
                target = max(int(requirement.get('target', 1)), 1)
                current = MissionActivity.objects.filter(
                    user=user,
                    mission=mission,
                    action=requirement_action,
                ).count()
                progress[key] = min(current, target)
                all_complete = all_complete and current >= target
            if not all_complete and row.rewarded_at:
                original = PointTransaction.objects.filter(
                    user=user,
                    action='mission_completed',
                    reference_type=Mission._meta.label_lower,
                    reference_id=str(mission.pk),
                    points__gt=0,
                    reversal__isnull=True,
                ).order_by('-created_at', '-pk').first()
                if original:
                    reverse_transaction(
                        original,
                        description=f'Мисията „{mission.title}“ вече не е завършена',
                    )
                row.completed_at = None
                row.rewarded_at = None
            row.progress = progress
            row.save(
                update_fields=('progress', 'completed_at', 'rewarded_at', 'updated_at')
            )
