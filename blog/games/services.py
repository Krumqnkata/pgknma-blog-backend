from datetime import datetime, time, timedelta
import random
import secrets

from django.db import transaction
from django.db.models import Max, Min, Sum
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound, ValidationError

from blog.gamification.config import daily_limit, point_value
from blog.gamification.services import change_points
from blog.models import PointTransaction, UserProfile
from .content import digest, robot_puzzle
from .definitions import DAYS, GAMES, PACK_VERSION, action_name
from .engine import InvalidMove, initial_state, move, public_state
from .models import GamePuzzle, GameRequest, GameSession, GameSettings


class Conflict(APIException):
    status_code = 409
    default_detail = 'Играта е обновена от друга заявка. Зареди текущото състояние.'


class GamesUnavailable(APIException):
    status_code = 503
    default_detail = 'Игрите още не са отворени. Опитай отново по-късно.'


def day_bounds(day):
    return (
        timezone.make_aware(datetime.combine(day, time.min)),
        timezone.make_aware(datetime.combine(day + timedelta(days=1), time.min)),
    )


def settings_row():
    return GameSettings.objects.filter(pk=1).first() or GameSettings()


def require_open():
    config = settings_row()
    if not config.enabled or not config.start_date or config.start_date > timezone.localdate():
        raise GamesUnavailable()
    return config


def earned_today(user, at=None):
    if not user or not user.is_authenticated:
        return 0
    start, end = day_bounds(timezone.localdate(at))
    return GameSession.objects.filter(user=user, finished_at__gte=start, finished_at__lt=end).aggregate(total=Sum('points_awarded'))['total'] or 0


def expire(session):
    if session.status == 'active' and session.expires_at <= timezone.now():
        session.status = 'expired'
        session.finished_at = timezone.now()
        session.revision += 1
        session.save(update_fields=('status', 'finished_at', 'revision'))


def session_payload(session):
    config = settings_row()
    return {
        'id': str(session.pk), 'puzzle_id': str(session.puzzle_id),
        'game': session.puzzle.game, 'mode': session.mode, 'status': session.status,
        'revision': session.revision, 'expires_at': session.expires_at,
        'reward_eligible': session.reward_eligible,
        'points_awarded': session.points_awarded, 'score': session.score,
        'earned_today': earned_today(session.user), 'daily_cap': config.daily_points_cap,
        'state': public_state(session.puzzle.game, session.puzzle.payload, session.state, session.status),
    }


def lock_user(user):
    UserProfile.objects.get_or_create(user=user)
    return UserProfile.objects.select_for_update().get(user=user)


def receipt(user, key, fingerprint):
    existing = GameRequest.objects.filter(user=user, key=key).select_related('session__puzzle').first()
    if existing:
        if existing.fingerprint != fingerprint:
            raise Conflict('Този идентификатор вече е използван за друго действие.')
        expire(existing.session)
        return existing.session


def fresh_practice(user, game):
    available = GamePuzzle.objects.filter(game=game, version=PACK_VERSION, pool='practice').exclude(sessions__user=user)
    count = available.count()
    if count:
        return available.order_by('slot')[secrets.randbelow(count)]
    # Procedural games continue beyond the prepared reserve. These boards are
    # persisted once, so a reload can never change their answer.
    rng = random.Random(secrets.token_hex(32))
    if game in ('robot', 'memory', 'code'):
        for _ in range(100):
            if game == 'robot':
                payload = robot_puzzle(rng)
            elif game == 'memory':
                cards = rng.sample(list('🍎🍋🍇🍒🥝🥕🌻🌵🌲🍁🐱🐶🦊🐻🐼🐸🦋🐝🐢🐬🚀🚲🚂🚁⚽🏀🎲🎯🎹🎸🔑💎'), 8)*2
                rng.shuffle(cards)
                payload = {'cards': cards}
            else:
                payload = {'secret': ''.join(rng.sample('0123456789', 4))}
            fingerprint = digest([game, payload])
            puzzle = GamePuzzle.objects.filter(fingerprint=fingerprint).first()
            if puzzle and (puzzle.pool == 'daily' or puzzle.sessions.filter(user=user).exists()):
                continue
            if puzzle:
                return puzzle
            puzzle, _ = GamePuzzle.objects.get_or_create(fingerprint=fingerprint, defaults={
                'key': f'generated:{fingerprint}', 'game': game, 'pool': 'generated',
                'slot': None, 'version': PACK_VERSION, 'payload': payload,
            })
            return puzzle
    # Finite banks rotate as explicitly labelled, non-rewarded practice.
    available = GamePuzzle.objects.filter(game=game, version=PACK_VERSION, pool='practice')
    count = available.count()
    if not count:
        raise GamesUnavailable('Няма подготвени задачи за тази игра.')
    return available.order_by('slot')[secrets.randbelow(count)]


@transaction.atomic
def start_session(user, game, mode, request_id, puzzle_id=None):
    config = require_open()
    lock_user(user)
    fingerprint = digest(['start', game, mode, str(puzzle_id) if puzzle_id else None])
    repeated = receipt(user, request_id, fingerprint)
    if repeated:
        return repeated
    today = timezone.localdate()
    # Expiration is also handled lazily; no cron is required for correctness.
    GameSession.objects.filter(user=user, status='active', expires_at__lte=timezone.now()).update(status='expired', finished_at=timezone.now())
    current = GameSession.objects.filter(user=user, puzzle__game=game, mode=mode, status='active').select_related('puzzle').first()
    if mode == 'replay':
        if not puzzle_id or not GameSession.objects.filter(user=user, puzzle_id=puzzle_id, status__in=('won','lost')).exists():
            raise ValidationError('Можеш да упражняваш само вече приключена задача.')
        puzzle = GamePuzzle.objects.get(pk=puzzle_id)
        if puzzle.game != game:
            raise ValidationError('Задачата е от друга игра.')
        if current and current.puzzle_id != puzzle.pk:
            current = None
    elif mode == 'daily':
        slot = (today - config.start_date).days % DAYS
        puzzle = GamePuzzle.objects.filter(game=game, pool='daily', slot=slot, version=config.pack_version).first()
        if not puzzle:
            raise GamesUnavailable('Дневната задача липсва. Опитай по-късно.')
        current = GameSession.objects.filter(user=user, puzzle=puzzle, mode='daily', day=today).select_related('puzzle').first()
    else:
        puzzle = current.puzzle if current else fresh_practice(user, game)
    if current:
        session = current
    else:
        eligible = mode != 'replay' and not GameSession.objects.filter(user=user, puzzle=puzzle).exists()
        session = GameSession.objects.create(
            user=user, puzzle=puzzle, mode=mode, day=today,
            state=initial_state(game), reward_eligible=eligible,
            expires_at=day_bounds(today)[1] if mode == 'daily' else timezone.now()+timedelta(hours=24),
        )
    GameRequest.objects.create(user=user, key=request_id, session=session, fingerprint=fingerprint)
    return session


@transaction.atomic
def get_session(user, session_id):
    lock_user(user)
    session = GameSession.objects.filter(pk=session_id, user=user).select_related('puzzle').first()
    if not session:
        raise NotFound()
    expire(session)
    return session


@transaction.atomic
def submit_action(user, session_id, request_id, revision, action):
    config = require_open()
    lock_user(user)
    fingerprint = digest(['move', str(session_id), revision, action])
    repeated = receipt(user, request_id, fingerprint)
    if repeated:
        return repeated
    session = GameSession.objects.filter(pk=session_id, user=user).select_related('puzzle').first()
    if not session:
        raise NotFound()
    expire(session)
    if session.status != 'active':
        return session
    if revision != session.revision:
        raise Conflict()
    try:
        state, status, score = move(session.puzzle.game, session.puzzle.payload, session.state, action)
    except InvalidMove as exc:
        raise ValidationError(str(exc)) from exc
    session.state, session.status, session.score = state, status, score
    session.revision += 1
    if status != 'active':
        session.finished_at = timezone.now()
    if status == 'won' and session.reward_eligible:
        action_key = action_name(session.puzzle.game)
        award = max(0, min(point_value(action_key), config.daily_points_cap - earned_today(user, session.finished_at)))
        limit = daily_limit(action_key)
        if limit is not None:
            start, end = day_bounds(timezone.localdate(session.finished_at))
            if GameSession.objects.filter(user=user, puzzle__game=session.puzzle.game, finished_at__gte=start, finished_at__lt=end, points_awarded__gt=0).count() >= limit:
                award = 0
        if award:
            result, created = change_points(
                user=user, points=award, action=action_key,
                description=GAMES[session.puzzle.game][0],
                idempotency_key=f'game:{user.pk}:{session.puzzle_id}',
                reference_type='blog.gamesession', reference_id=str(session.pk),
                source=PointTransaction.Source.ACTIVITY,
            )
            session.points_awarded = result.points if created else 0
    session.save()
    GameRequest.objects.create(user=user, key=request_id, session=session, fingerprint=fingerprint)
    return session


def catalog(user):
    config = settings_row()
    today = timezone.localdate()
    enabled = bool(config.enabled and config.start_date and config.start_date <= today)
    games = []
    for slug, (title, rules, icon) in GAMES.items():
        item = {'slug': slug, 'title': title, 'rules': rules, 'icon': icon, 'points': point_value(action_name(slug)), 'daily_status': None, 'best_score': None, 'wins': 0}
        if user and user.is_authenticated:
            sessions = GameSession.objects.filter(user=user, puzzle__game=slug)
            row = sessions.filter(day=today, mode='daily').first()
            item['daily_status'] = ('expired' if row.status == 'active' and row.expires_at <= timezone.now() else row.status) if row else None
            wins = sessions.filter(status='won')
            item['wins'] = wins.count()
            item['best_score'] = wins.aggregate(best=Max('score') if slug == 'quiz' else Min('score'))['best']
        games.append(item)
    return {'enabled': enabled, 'start_date': config.start_date, 'day': today, 'daily_cap': config.daily_points_cap, 'earned_today': earned_today(user), 'games': games}
