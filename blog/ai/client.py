"""Small client for the Cloudflare Workers AI REST API."""

import logging
from dataclasses import dataclass, field

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# Cloudflare answers with one of these error codes when the free daily
# allocation of neurons is used up.
QUOTA_ERROR_CODES = {3036, 4006}


class AIError(Exception):
    """Base class for every AI failure. Callers must never let it break a page."""


class AIUnavailable(AIError):
    """No credentials, no network, a Cloudflare error or an empty answer."""


class AIQuotaExceeded(AIError):
    """The daily free allocation (or a Cloudflare rate limit) is exhausted."""


@dataclass
class AIResult:
    text: str
    model: str
    usage: dict = field(default_factory=dict)


def credentials_configured():
    return bool(settings.CF_ACCOUNT_ID and settings.CF_API_TOKEN)


def _model_options(model):
    """Per-model request differences found while testing the models."""
    if 'gemma-4' in model:
        # Gemma 4 is a reasoning model: without this it spends the whole token
        # budget "thinking" in English and returns an empty answer.
        return 'max_completion_tokens', {'chat_template_kwargs': {'enable_thinking': False}}
    return 'max_tokens', {}


def extract_text(result):
    """Models answer either with {'response': ...} or in the OpenAI format."""
    if not isinstance(result, dict):
        return ''
    if isinstance(result.get('response'), str):
        return result['response'].strip()
    choices = result.get('choices') or []
    if choices and isinstance(choices[0], dict):
        message = choices[0].get('message') or {}
        return (message.get('content') or choices[0].get('text') or '').strip()
    return ''


def _error_codes(payload):
    errors = payload.get('errors') if isinstance(payload, dict) else None
    codes = set()
    for item in errors or []:
        if isinstance(item, dict) and isinstance(item.get('code'), int):
            codes.add(item['code'])
    return codes


def run_chat(*, model, messages, max_tokens=400, temperature=0.3):
    """Send a chat request and return the answer text.

    Raises AIUnavailable or AIQuotaExceeded; never returns an empty text.
    """
    if not credentials_configured():
        raise AIUnavailable('Липсват CF_ACCOUNT_ID и/или CF_API_TOKEN.')

    token_param, extra = _model_options(model)
    body = {
        'messages': messages,
        'temperature': temperature,
        token_param: max_tokens,
        **extra,
    }
    url = f'{settings.AI_API_BASE_URL}/accounts/{settings.CF_ACCOUNT_ID}/ai/run/{model}'
    try:
        response = requests.post(
            url,
            json=body,
            headers={'Authorization': f'Bearer {settings.CF_API_TOKEN}'},
            timeout=(5, settings.AI_REQUEST_TIMEOUT_SECONDS),
        )
    except requests.RequestException as exc:
        raise AIUnavailable('Няма връзка с Cloudflare Workers AI.') from exc

    try:
        payload = response.json()
    except ValueError:
        payload = {}

    codes = _error_codes(payload)
    if response.status_code == 429 or codes & QUOTA_ERROR_CODES:
        raise AIQuotaExceeded('Дневният безплатен лимит на Workers AI е изчерпан.')
    if response.status_code >= 400 or not payload.get('success', False):
        # Never log the request body: it can contain text written by students.
        logger.warning(
            'Workers AI request failed: status=%s codes=%s model=%s',
            response.status_code,
            sorted(codes),
            model,
        )
        raise AIUnavailable(f'Cloudflare върна грешка (HTTP {response.status_code}).')

    result = payload.get('result') or {}
    text = extract_text(result)
    if not text:
        raise AIUnavailable('Моделът върна празен отговор.')
    usage = result.get('usage') if isinstance(result.get('usage'), dict) else {}
    return AIResult(text=text, model=model, usage=usage)
