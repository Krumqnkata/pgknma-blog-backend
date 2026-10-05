from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from blog.permissions import SiteIsAvailable
from .definitions import GAME_CHOICES
from .services import catalog, get_session, session_payload, start_session, submit_action


class GameThrottle(UserRateThrottle):
    scope = 'games'
    rate = '120/minute'


class StartSerializer(serializers.Serializer):
    game = serializers.ChoiceField(choices=GAME_CHOICES)
    mode = serializers.ChoiceField(choices=('daily', 'practice', 'replay'))
    request_id = serializers.UUIDField()
    puzzle_id = serializers.UUIDField(required=False)


class ActionSerializer(serializers.Serializer):
    request_id = serializers.UUIDField()
    revision = serializers.IntegerField(min_value=0)
    action = serializers.DictField()

    def validate_action(self, value):
        import json
        if len(json.dumps(value)) > 3000:
            raise serializers.ValidationError('Твърде голямо действие.')
        return value


class SessionSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    puzzle_id = serializers.UUIDField()
    game = serializers.CharField()
    mode = serializers.CharField()
    status = serializers.CharField()
    revision = serializers.IntegerField()
    expires_at = serializers.DateTimeField()
    reward_eligible = serializers.BooleanField()
    points_awarded = serializers.IntegerField()
    score = serializers.IntegerField(allow_null=True)
    earned_today = serializers.IntegerField()
    daily_cap = serializers.IntegerField()
    state = serializers.JSONField(help_text='Visible game state only; discriminated by game and status.')


class GameCatalogView(APIView):
    permission_classes = (SiteIsAvailable, AllowAny)

    @extend_schema(tags=['Игри'], responses=inline_serializer(name='GameCatalog', fields={
        'enabled': serializers.BooleanField(), 'start_date': serializers.DateField(allow_null=True),
        'day': serializers.DateField(), 'daily_cap': serializers.IntegerField(),
        'earned_today': serializers.IntegerField(), 'games': serializers.ListField(child=serializers.DictField()),
    }))
    def get(self, request):
        return Response(catalog(request.user), headers={'Cache-Control': 'private, no-store'})


class GameStartView(APIView):
    permission_classes = (SiteIsAvailable, IsAuthenticated)
    throttle_classes = (GameThrottle,)

    @extend_schema(tags=['Игри'], request=StartSerializer, responses=SessionSerializer)
    def post(self, request):
        serializer = StartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        session = start_session(request.user, **serializer.validated_data)
        return Response(session_payload(session), headers={'Cache-Control': 'private, no-store'})


class GameSessionView(APIView):
    permission_classes = (SiteIsAvailable, IsAuthenticated)
    throttle_classes = (GameThrottle,)

    @extend_schema(tags=['Игри'], responses=SessionSerializer)
    def get(self, request, session_id):
        return Response(session_payload(get_session(request.user, session_id)), headers={'Cache-Control': 'private, no-store'})


class GameActionView(GameSessionView):
    http_method_names = ('post', 'options')
    @extend_schema(tags=['Игри'], request=ActionSerializer, responses=SessionSerializer)
    def post(self, request, session_id):
        serializer = ActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        session = submit_action(request.user, session_id, **serializer.validated_data)
        return Response(session_payload(session), headers={'Cache-Control': 'private, no-store'})
