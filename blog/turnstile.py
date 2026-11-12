"""Server-side Cloudflare Turnstile verification for new features.

The login and registration serializers keep their own copies on purpose; this
helper is used by the AI assistant.
"""

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def verify_turnstile(token, remote_ip=None):
    """Return True only when Cloudflare confirms the token."""
    secret_key = (getattr(settings, 'TURNSTILE_SECRET_KEY', '') or '').strip()
    if not secret_key or not token:
        return False
    payload = {'secret': secret_key, 'response': token}
    if remote_ip:
        payload['remoteip'] = remote_ip
    try:
        response = requests.post(settings.TURNSTILE_VERIFY_URL, data=payload, timeout=5)
        response.raise_for_status()
        return bool(response.json().get('success'))
    except (requests.RequestException, ValueError):
        logger.warning('Turnstile verification request failed.')
        return False
