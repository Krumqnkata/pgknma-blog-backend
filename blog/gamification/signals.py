from django.db.models.signals import m2m_changed, post_save, pre_delete, pre_save
from django.dispatch import receiver

from blog.models import Badge, BellSongSuggestion, Comments, DigitalReward, MemeOfWeek, PollAnswer

from .services import (
    award_activity,
    record_mission_activity,
    remove_mission_activity,
    reverse_activity,
    sync_badge_rewards,
)


@receiver(post_save, sender=Comments)
def reward_new_comment(sender, instance, created, raw=False, **kwargs):
    if created and not raw and instance.user_id:
        award_activity(instance.user, 'comment', instance)
        record_mission_activity(instance.user, 'comment', instance.pk)


@receiver(pre_delete, sender=Comments)
def reverse_deleted_comment(sender, instance, **kwargs):
    reverse_activity(instance, 'comment', 'Изтрит коментар')
    if instance.user_id:
        remove_mission_activity(instance.user, 'comment', instance.pk)


@receiver(post_save, sender=PollAnswer)
def reward_poll_answer(sender, instance, created, raw=False, **kwargs):
    if created and not raw and instance.user_id:
        award_activity(instance.user, 'poll_vote', instance)
        record_mission_activity(instance.user, 'poll_vote', instance.question_id)


@receiver(pre_delete, sender=PollAnswer)
def reverse_deleted_poll_answer(sender, instance, **kwargs):
    reverse_activity(instance, 'poll_vote', 'Премахнат отговор в анкета')
    if instance.user_id:
        remove_mission_activity(instance.user, 'poll_vote', instance.question_id)


def _remember_status(model, instance):
    if not instance.pk:
        instance._gamification_previous_status = None
        return
    instance._gamification_previous_status = (
        model.objects.filter(pk=instance.pk).values_list('status', flat=True).first()
    )


@receiver(pre_save, sender=BellSongSuggestion)
def remember_song_status(sender, instance, **kwargs):
    _remember_status(BellSongSuggestion, instance)


@receiver(post_save, sender=BellSongSuggestion)
def sync_song_reward(sender, instance, raw=False, **kwargs):
    if raw or not instance.user_id:
        return
    previous = getattr(instance, '_gamification_previous_status', None)
    if instance.status == 'approved' and previous != 'approved':
        award_activity(instance.user, 'song_approved', instance)
    elif previous == 'approved' and instance.status != 'approved':
        reverse_activity(instance, 'song_approved', 'Отменено одобрение на песен')


@receiver(pre_delete, sender=BellSongSuggestion)
def reverse_deleted_song(sender, instance, **kwargs):
    reverse_activity(instance, 'song_approved', 'Премахнато предложение за песен')


@receiver(pre_save, sender=MemeOfWeek)
def remember_meme_status(sender, instance, **kwargs):
    _remember_status(MemeOfWeek, instance)


@receiver(post_save, sender=MemeOfWeek)
def sync_meme_reward(sender, instance, raw=False, **kwargs):
    if raw or not instance.user_id:
        return
    previous = getattr(instance, '_gamification_previous_status', None)
    if instance.status == 'approved' and previous != 'approved':
        award_activity(instance.user, 'meme_approved', instance)
    elif previous == 'approved' and instance.status != 'approved':
        reverse_activity(instance, 'meme_approved', 'Отменено одобрение на меме')


@receiver(pre_delete, sender=MemeOfWeek)
def reverse_deleted_meme(sender, instance, **kwargs):
    reverse_activity(instance, 'meme_approved', 'Премахнато одобрено меме')


@receiver(m2m_changed, sender=Badge.digital_rewards.through)
def backfill_linked_digital_rewards(sender, instance, action, reverse=False, **kwargs):
    if action != 'post_add':
        return
    if reverse:
        for badge in instance.badges.all():
            sync_badge_rewards(badge)
        return
    sync_badge_rewards(instance)


@receiver(post_save, sender=DigitalReward)
def backfill_reactivated_digital_reward(sender, instance, raw=False, **kwargs):
    if raw or not instance.active:
        return
    for badge in instance.badges.all():
        sync_badge_rewards(badge)
