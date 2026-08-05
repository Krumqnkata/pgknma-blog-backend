from datetime import timedelta

from django.contrib.sessions.models import Session as DjangoSession
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken

from valentine.models import Register, ValentineLoginAttempt

from .models import BlogLoginSession, ConsentRecord, EmailVerificationToken


def cleanup_personal_data(*, days, now=None, dry_run=False):
    """Apply the configured retention window to security/network metadata."""
    if days < 1:
        raise ValueError('Retention days must be a positive integer.')

    now = now or timezone.now()
    cutoff = now - timedelta(days=days)

    old_consents = ConsentRecord.objects.filter(timestamp__lt=cutoff)
    old_blog_sessions = BlogLoginSession.objects.filter(created_at__lt=cutoff)
    old_valentine_sessions = Register.objects.filter(created_at__lt=cutoff)
    old_valentine_attempts = ValentineLoginAttempt.objects.filter(
        updated_at__lt=cutoff
    )
    expired_django_sessions = DjangoSession.objects.filter(expire_date__lt=now)
    expired_jwt_tokens = OutstandingToken.objects.filter(expires_at__lt=now)
    old_email_verification_tokens = EmailVerificationToken.objects.filter(
        Q(expires_at__lt=cutoff) | Q(used_at__lt=cutoff)
    )

    counts = {
        'consent_records': old_consents.count(),
        'blog_login_sessions': old_blog_sessions.count(),
        'valentine_sessions': old_valentine_sessions.count(),
        'valentine_login_attempts': old_valentine_attempts.count(),
        'expired_django_sessions': expired_django_sessions.count(),
        'expired_jwt_tokens': expired_jwt_tokens.count(),
        'email_verification_tokens': old_email_verification_tokens.count(),
    }
    if dry_run:
        return counts

    with transaction.atomic():
        # Deleting the persisted BlogLoginSession invalidates both access and
        # refresh tokens immediately through SessionJWTAuthentication.
        old_blog_sessions.delete()
        old_consents.delete()
        old_valentine_sessions.delete()
        old_valentine_attempts.delete()
        expired_django_sessions.delete()
        expired_jwt_tokens.delete()
        old_email_verification_tokens.delete()

    return counts
