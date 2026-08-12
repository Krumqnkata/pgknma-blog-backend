from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from .automatic_notifications import (
    create_notification_once,
    notify_event_if_published,
    notify_mission_if_available,
    notify_poll_if_active,
    notify_post_if_due,
)
from .models import (
    BellSongSuggestion,
    Comments,
    Event,
    MemeOfWeek,
    Mission,
    PollQuestion,
    Posts,
)
from .notification_categories import COMMENT_REPLY, SUBMISSION_STATUS


@receiver(post_save, sender=Posts, dispatch_uid='blog.notify_first_post_publication')
def notify_first_post_publication(sender, instance, **kwargs):
    notify_post_if_due(instance.pk)


@receiver(post_save, sender=Event, dispatch_uid='blog.notify_first_event_publication')
def notify_first_event_publication(sender, instance, **kwargs):
    notify_event_if_published(instance.pk)


@receiver(post_save, sender=PollQuestion, dispatch_uid='blog.notify_first_poll_activation')
def notify_first_poll_activation(sender, instance, **kwargs):
    notify_poll_if_active(instance.pk)


@receiver(post_save, sender=Mission, dispatch_uid='blog.notify_first_mission_availability')
def notify_first_mission_availability(sender, instance, **kwargs):
    notify_mission_if_available(instance.pk)


@receiver(post_save, sender=Comments, dispatch_uid='blog.notify_comment_reply')
def notify_comment_reply(sender, instance, created, **kwargs):
    if not created or not instance.parent_id or not instance.parent.user_id:
        return
    if instance.user_id and instance.user_id == instance.parent.user_id:
        return
    author = instance.user.username if instance.user_id else 'Посетител'
    create_notification_once(
        key=f'comment-reply:{instance.pk}',
        text=f'{author} отговори на Ваш коментар.',
        category=COMMENT_REPLY,
        action_url=f'/post/{instance.post_id}#comment-{instance.parent_id}',
        user=instance.parent.user,
    )


def _remember_previous_status(sender, instance, **kwargs):
    if not instance.pk:
        instance._notification_previous_status = None
        return
    instance._notification_previous_status = sender.objects.filter(
        pk=instance.pk
    ).values_list('status', flat=True).first()


pre_save.connect(
    _remember_previous_status,
    sender=BellSongSuggestion,
    dispatch_uid='blog.remember_song_status_for_notification',
)
pre_save.connect(
    _remember_previous_status,
    sender=MemeOfWeek,
    dispatch_uid='blog.remember_meme_status_for_notification',
)


def _notify_submission_status(instance, *, kind, action_url):
    if not instance.user_id or instance.status not in ('approved', 'rejected'):
        return
    if getattr(instance, '_notification_previous_status', None) == instance.status:
        return
    status_text = 'одобрено' if instance.status == 'approved' else 'отхвърлено'
    create_notification_once(
        key=f'{kind}-status:{instance.pk}:{instance.status}',
        text=f'Вашето предложение „{instance.title or kind}“ беше {status_text}.',
        category=SUBMISSION_STATUS,
        action_url=action_url,
        user=instance.user,
    )


@receiver(post_save, sender=BellSongSuggestion, dispatch_uid='blog.notify_song_status')
def notify_song_status(sender, instance, **kwargs):
    _notify_submission_status(instance, kind='песен', action_url='/profile?tab=songs')


@receiver(post_save, sender=MemeOfWeek, dispatch_uid='blog.notify_meme_status')
def notify_meme_status(sender, instance, **kwargs):
    _notify_submission_status(instance, kind='меме', action_url='/profile?tab=memes')
