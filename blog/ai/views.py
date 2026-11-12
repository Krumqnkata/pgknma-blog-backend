import hashlib

from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from blog.network import get_client_ip
from blog.permissions import SiteIsAvailable
from blog.throttling import TrustedProxyScopedRateThrottle
from blog.turnstile import verify_turnstile

from . import config
from .assistant import answer_question
from .client import AIQuotaExceeded, AIUnavailable

SESSION_SALT = 'blog.ai.assistant-session'
SESSION_MAX_AGE_SECONDS = 60 * 60
MAX_HISTORY_TURNS = 4


class AssistantTurnSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=('user', 'assistant'))
    content = serializers.CharField(max_length=1000, trim_whitespace=True)


class AssistantRequestSerializer(serializers.Serializer):
    question = serializers.CharField(max_length=500, trim_whitespace=True)
    history = AssistantTurnSerializer(many=True, required=False)
    turnstile_token = serializers.CharField(max_length=2048, required=False, allow_blank=True)
    ai_session = serializers.CharField(max_length=300, required=False, allow_blank=True)

    def validate_history(self, value):
        return value[-MAX_HISTORY_TURNS:]


def _error(message, code, http_status):
    return Response({'detail': message, 'code': code}, status=http_status)


def _client_fingerprint(request):
    ip = get_client_ip(request) or 'unknown'
    return hashlib.sha256(f'{settings.SECRET_KEY}:{ip}'.encode()).hexdigest()[:32]


def issue_session(request):
    return signing.TimestampSigner(salt=SESSION_SALT).sign(_client_fingerprint(request))


def session_is_valid(request, token):
    if not token:
        return False
    try:
        value = signing.TimestampSigner(salt=SESSION_SALT).unsign(token, max_age=SESSION_MAX_AGE_SECONDS)
    except signing.BadSignature:
        return False
    return value == _client_fingerprint(request)


def _daily_counter_key():
    return f'ai:assistant:questions:{timezone.now().strftime("%Y-%m-%d")}'


def reserve_daily_question():
    """Count one question for today (UTC). False when the site-wide cap is reached."""
    key = _daily_counter_key()
    cache.add(key, 0, timeout=60 * 60 * 48)
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=60 * 60 * 48)
        count = 1
    if count > settings.AI_ASSISTANT_DAILY_LIMIT:
        return False
    return True


def release_daily_question():
    try:
        cache.decr(_daily_counter_key())
    except ValueError:
        pass


class AIAssistantView(APIView):
    """POST /api/ai/assistant/ — questions are answered and never stored."""

    permission_classes = [SiteIsAvailable, AllowAny]

    def get_throttles(self):
        self.throttle_scope = (
            'ai_assistant_user' if self.request.user.is_authenticated else 'ai_assistant_anon'
        )
        return [TrustedProxyScopedRateThrottle()]

    def post(self, request):
        if not config.ai_feature_enabled(config.ASSISTANT):
            return _error('ИИ асистентът в момента е изключен.', 'ai_disabled', status.HTTP_503_SERVICE_UNAVAILABLE)

        serializer = AssistantRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        new_session = None
        if not request.user.is_authenticated and not session_is_valid(request, data.get('ai_session')):
            token = data.get('turnstile_token')
            if not token:
                return _error('Потвърдете, че не сте робот.', 'turnstile_required', status.HTTP_400_BAD_REQUEST)
            if not verify_turnstile(token, get_client_ip(request)):
                return _error(
                    'Проверката за робот не успя. Опитайте отново.',
                    'turnstile_failed',
                    status.HTTP_400_BAD_REQUEST,
                )
            new_session = issue_session(request)

        if not reserve_daily_question():
            return _error(
                'Асистентът почива до утре — дневният лимит от въпроси е достигнат.',
                'ai_daily_limit',
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        try:
            result = answer_question(data['question'], data.get('history') or [])
        except AIQuotaExceeded:
            release_daily_question()
            return _error(
                'Асистентът почива до утре — дневният лимит е достигнат.',
                'ai_daily_limit',
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except AIUnavailable:
            release_daily_question()
            return _error(
                'Асистентът не е достъпен в момента. Опитайте след малко.',
                'ai_unavailable',
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        payload = {'answer': result.answer, 'sources': result.sources}
        if new_session:
            payload['ai_session'] = new_session
        return Response(payload)
