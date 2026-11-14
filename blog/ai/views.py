import hashlib

from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from blog.models import AssistantConversation, AssistantMessage
from blog.network import get_client_ip
from blog.permissions import SiteIsAvailable
from blog.throttling import TrustedProxyScopedRateThrottle
from blog.turnstile import verify_turnstile

from . import config
from .assistant import answer_question

SESSION_SALT = 'blog.ai.assistant-session'
SESSION_MAX_AGE_SECONDS = 60 * 60
MAX_HISTORY_TURNS = 6
CONVERSATION_LIST_SIZE = 20
TITLE_CHARS = 80


class AssistantTurnSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=('user', 'assistant'))
    content = serializers.CharField(max_length=1000, trim_whitespace=True)


class AssistantRequestSerializer(serializers.Serializer):
    question = serializers.CharField(max_length=500, trim_whitespace=True)
    history = AssistantTurnSerializer(many=True, required=False)
    conversation_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)
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
    """Count one AI answer for today (UTC). False when the site-wide cap is reached.

    Rule answers do not count: they cost nothing.
    """
    key = _daily_counter_key()
    cache.add(key, 0, timeout=60 * 60 * 48)
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=60 * 60 * 48)
        count = 1
    return count <= settings.AI_ASSISTANT_DAILY_LIMIT


def release_daily_question():
    try:
        cache.decr(_daily_counter_key())
    except ValueError:
        pass


def _message_payload(message):
    return {
        'id': message.pk,
        'role': message.role,
        'content': message.content,
        'answer_source': message.answer_source,
        'sources': message.sources or [],
        'created_at': message.created_at.isoformat(),
    }


def _conversation_history(conversation):
    recent = list(conversation.messages.order_by('-created_at', '-pk')[:MAX_HISTORY_TURNS])
    return [{'role': message.role, 'content': message.content} for message in reversed(recent)]


class AIAssistantView(APIView):
    """POST /api/ai/assistant/

    Logged-in users: the conversation is stored (see AssistantConversation).
    Anonymous visitors: nothing is stored; the browser keeps the conversation.
    """

    permission_classes = [SiteIsAvailable, AllowAny]

    def get_throttles(self):
        self.throttle_scope = (
            'ai_assistant_user' if self.request.user.is_authenticated else 'ai_assistant_anon'
        )
        return [TrustedProxyScopedRateThrottle()]

    def post(self, request):
        site_settings = config.get_site_settings()
        if not config.assistant_visible(site_settings):
            return _error('ИИ асистентът в момента е изключен.', 'ai_disabled', status.HTTP_503_SERVICE_UNAVAILABLE)

        serializer = AssistantRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        user = request.user if request.user.is_authenticated else None

        new_session = None
        if user is None and not session_is_valid(request, data.get('ai_session')):
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

        conversation = None
        if user is not None:
            if data.get('conversation_id'):
                conversation = get_object_or_404(AssistantConversation, pk=data['conversation_id'], user=user)
                history = _conversation_history(conversation)
            else:
                history = []
        else:
            history = data.get('history') or []

        result = answer_question(
            data['question'],
            user=user,
            history=history,
            site_settings=site_settings,
            reserve_ai=reserve_daily_question,
            release_ai=release_daily_question,
        )

        payload = {
            'answer': result.answer,
            'sources': result.sources,
            'answer_source': result.answer_source,
        }
        if user is not None:
            with transaction.atomic():
                if conversation is None:
                    conversation = AssistantConversation.objects.create(
                        user=user,
                        title=data['question'][:TITLE_CHARS],
                    )
                AssistantMessage.objects.create(conversation=conversation, role='user', content=data['question'])
                answer = AssistantMessage.objects.create(
                    conversation=conversation,
                    role='assistant',
                    content=result.answer,
                    answer_source=result.answer_source,
                    rule_key=result.rule_key,
                    sources=result.sources,
                )
                AssistantConversation.objects.filter(pk=conversation.pk).update(updated_at=timezone.now())
            payload['conversation_id'] = conversation.pk
            payload['message_id'] = answer.pk
        if new_session:
            payload['ai_session'] = new_session
        return Response(payload)


class AssistantConversationListView(APIView):
    """GET /api/ai/conversations/ — the user's latest conversations."""

    permission_classes = [SiteIsAvailable, IsAuthenticated]

    def get(self, request):
        conversations = AssistantConversation.objects.filter(user=request.user).order_by('-updated_at')[:CONVERSATION_LIST_SIZE]
        return Response([
            {'id': item.pk, 'title': item.title, 'updated_at': item.updated_at.isoformat()}
            for item in conversations
        ])


class AssistantConversationDetailView(APIView):
    """GET / DELETE /api/ai/conversations/<id>/ — only the owner can see or delete it."""

    permission_classes = [SiteIsAvailable, IsAuthenticated]

    def _conversation(self, request, pk):
        return get_object_or_404(AssistantConversation, pk=pk, user=request.user)

    def get(self, request, pk):
        conversation = self._conversation(request, pk)
        return Response({
            'id': conversation.pk,
            'title': conversation.title,
            'updated_at': conversation.updated_at.isoformat(),
            'messages': [_message_payload(message) for message in conversation.messages.order_by('created_at', 'pk')],
        })

    def delete(self, request, pk):
        self._conversation(request, pk).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
