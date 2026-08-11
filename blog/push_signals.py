from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Notification
from .push import send_notification_push


@receiver(post_save, sender=Notification, dispatch_uid='blog.send_new_notification_push')
def send_new_notification_push(sender, instance, created, **kwargs):
    if created and instance.enabled:
        transaction.on_commit(lambda: send_notification_push(instance.pk))
