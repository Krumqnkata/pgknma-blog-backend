from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Notification
from .push import (
    send_notification_push,
    snapshot_notification_deliveries,
    web_push_is_configured,
)


@receiver(post_save, sender=Notification, dispatch_uid='blog.send_new_notification_push')
def send_new_notification_push(sender, instance, created, **kwargs):
    if created and instance.enabled and web_push_is_configured():
        # Recipient rows are created inside the same transaction as the
        # notification. A later retry therefore cannot discover new devices.
        snapshot_notification_deliveries(instance.pk)
        transaction.on_commit(lambda: send_notification_push(instance.pk))
