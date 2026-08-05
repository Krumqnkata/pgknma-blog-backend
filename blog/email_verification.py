"""Secure, one-time email verification through Django's Resend backend."""

from dataclasses import dataclass
from datetime import timedelta
import hashlib
import logging
import secrets
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.html import strip_tags

from .models import EmailVerificationToken, SiteSettings, UserProfile


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VerificationDeliveryResult:
    sent: bool
    auto_verified: bool


def _token_hash(raw_token):
    return hashlib.sha256(raw_token.encode('utf-8')).hexdigest()


def _mark_verified(user, *, verified_at=None):
    verified_at = verified_at or timezone.now()
    profile, _ = UserProfile.objects.get_or_create(user=user)
    if not profile.email_verified or not profile.email_verified_at:
        profile.email_verified = True
        profile.email_verified_at = verified_at
        profile.save(update_fields=('email_verified', 'email_verified_at'))
    EmailVerificationToken.objects.filter(
        user=user,
        used_at__isnull=True,
    ).update(used_at=verified_at)
    return profile


def mark_google_email_verified(user):
    """Google's signed email_verified claim is the verification authority."""

    with transaction.atomic():
        locked_user = User.objects.select_for_update().get(pk=user.pk)
        _mark_verified(locked_user)


def resend_is_on_cooldown(user):
    seconds = max(
        0,
        getattr(settings, 'EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS', 60),
    )
    if not seconds:
        return False
    cutoff = timezone.now() - timedelta(seconds=seconds)
    return EmailVerificationToken.objects.filter(
        user=user,
        used_at__isnull=True,
        created_at__gte=cutoff,
    ).exists()


def _create_token(user):
    ttl_seconds = max(
        300,
        getattr(settings, 'EMAIL_VERIFICATION_TOKEN_TTL_SECONDS', 86400),
    )
    now = timezone.now()
    raw_token = secrets.token_urlsafe(32)
    with transaction.atomic():
        locked_user = User.objects.select_for_update().get(pk=user.pk)
        EmailVerificationToken.objects.filter(
            user=locked_user,
            used_at__isnull=True,
        ).update(used_at=now)
        token = EmailVerificationToken.objects.create(
            user=locked_user,
            token_hash=_token_hash(raw_token),
            expires_at=now + timedelta(seconds=ttl_seconds),
        )
    return raw_token, token


def _verification_url(raw_token):
    path = reverse('email-verification-confirm')
    return f'{settings.PUBLIC_SITE_URL}{path}?{urlencode({"token": raw_token})}'


def send_verification_email(user):
    """Create a fresh token and synchronously send it through the Resend API."""

    raw_token, token = _create_token(user)
    expiry_time = timezone.localtime(token.expires_at).strftime(
        '%d.%m.%Y г. в %H:%M'
    )
    html_body = render_to_string(
        'email/verify-email.html',
        {
            'username': user.username,
            'verification_url': _verification_url(raw_token),
            'expiry_time': expiry_time,
        },
    )
    message = EmailMultiAlternatives(
        subject='Потвърдете имейла си в ПГКНМА Блог',
        body=strip_tags(html_body),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[user.email],
        reply_to=(
            [settings.EMAIL_VERIFICATION_REPLY_TO]
            if settings.EMAIL_VERIFICATION_REPLY_TO
            else None
        ),
    )
    message.attach_alternative(html_body, 'text/html')

    try:
        message.send(fail_silently=False)
        return VerificationDeliveryResult(sent=True, auto_verified=False)
    except Exception:
        logger.exception(
            'Email verification delivery failed for user %s',
            user.pk,
        )
        now = timezone.now()
        EmailVerificationToken.objects.filter(
            pk=token.pk,
            used_at__isnull=True,
        ).update(used_at=now)
        site_settings, _ = SiteSettings.objects.get_or_create(pk=1)
        if site_settings.auto_verify_on_email_failure:
            with transaction.atomic():
                locked_user = User.objects.select_for_update().get(pk=user.pk)
                _mark_verified(locked_user, verified_at=now)
            return VerificationDeliveryResult(sent=False, auto_verified=True)
        return VerificationDeliveryResult(sent=False, auto_verified=False)


def verify_email_token(raw_token):
    """Consume a raw token and return a redirect-safe status string."""

    if not raw_token or len(raw_token) > 512:
        return 'invalid'

    token_digest = _token_hash(raw_token)
    now = timezone.now()
    with transaction.atomic():
        token = (
            EmailVerificationToken.objects.select_for_update()
            .select_related('user')
            .filter(token_hash=token_digest)
            .first()
        )
        if token is None:
            return 'invalid'

        profile, _ = UserProfile.objects.select_for_update().get_or_create(
            user=token.user
        )
        if profile.email_verified:
            if token.used_at is None:
                token.used_at = now
                token.save(update_fields=('used_at',))
            return 'already-verified'
        if token.used_at is not None:
            return 'invalid'
        if token.expires_at <= now:
            return 'expired'

        _mark_verified(token.user, verified_at=now)
        return 'verified'
