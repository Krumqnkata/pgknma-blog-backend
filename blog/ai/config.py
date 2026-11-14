"""Which AI features are switched on.

A feature runs only when the main admin switch, its own switch and the
Cloudflare credentials are all present.
"""

from blog.models import SiteSettings

from .client import credentials_configured
from .models_catalog import DEFAULT_PUBLIC_MODEL, DEFAULT_STUDENT_TEXT_MODEL

SUMMARIES = 'summaries'
MODERATION = 'moderation'
ASSISTANT = 'assistant'

_FEATURE_FIELDS = {
    SUMMARIES: 'enable_ai_summaries',
    MODERATION: 'enable_ai_moderation',
    ASSISTANT: 'enable_ai_assistant',
}


def get_site_settings():
    site_settings, _ = SiteSettings.objects.get_or_create(pk=1)
    return site_settings


def public_model(site_settings=None):
    """Model for public content (article summaries), chosen in the admin."""
    site_settings = site_settings or get_site_settings()
    return site_settings.ai_model_public or DEFAULT_PUBLIC_MODEL


def student_text_model(site_settings=None):
    """Model for text written by students (moderation, assistant)."""
    site_settings = site_settings or get_site_settings()
    return site_settings.ai_model_student_text or DEFAULT_STUDENT_TEXT_MODEL


def ai_feature_enabled(feature, site_settings=None):
    """The feature may call Cloudflare: admin switches on and credentials present."""
    if feature not in _FEATURE_FIELDS:
        raise ValueError(f'Unknown AI feature: {feature}')
    if not credentials_configured():
        return False
    site_settings = site_settings or get_site_settings()
    return bool(site_settings.enable_ai and getattr(site_settings, _FEATURE_FIELDS[feature]))


def assistant_visible(site_settings=None):
    """The assistant is shown. It answers from its rules even without Cloudflare."""
    site_settings = site_settings or get_site_settings()
    return bool(site_settings.enable_ai and site_settings.enable_ai_assistant)


def assistant_ai_enabled(site_settings=None):
    """Questions without a matching rule may go to the AI model."""
    site_settings = site_settings or get_site_settings()
    return bool(
        assistant_visible(site_settings)
        and site_settings.ai_assistant_use_ai
        and credentials_configured()
    )
