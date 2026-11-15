"""Signals that post automatic messages to the team chat."""

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from . import team_chat_bot
from .models import ContactSubmission


@receiver(post_save, sender=ContactSubmission, dispatch_uid='team_chat_contact_received')
def announce_contact_submission(sender, instance, created, raw=False, **kwargs):
    if created and not raw:
        transaction.on_commit(lambda: team_chat_bot.contact_received(instance))
