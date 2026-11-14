from django.conf import settings
from blog.games.definitions import GAMES, action_name


DEFAULT_POINT_RULES = {
    'poll_vote': 5,
    'comment': 5,
    'song_approved': 20,
    'meme_approved': 20,
    'meme_week_winner': 50,
    'post_read': 2,
    'calendar_visit': 2,
}

DEFAULT_DAILY_LIMITS = {
    'comment': 5,
    'post_read': 5,
    'calendar_visit': 1,
}

ACTION_DESCRIPTIONS = {
    'poll_vote': 'Гласуване в анкета',
    'comment': 'Валиден коментар',
    'song_approved': 'Одобрено предложение за песен',
    'meme_approved': 'Одобрено меме',
    'meme_week_winner': 'Меме на седмицата',
    'post_read': 'Прочетена публикация',
    'calendar_visit': 'Посещение на училищния календар',
}

DEFAULT_POINT_RULES.update({action_name(game): 5 for game in GAMES})
ACTION_DESCRIPTIONS.update({action_name(game): f'Игра: {value[0]}' for game, value in GAMES.items()})


def point_value(action):
    from blog.models import GamificationPointRule

    rule = GamificationPointRule.objects.filter(action=action).first()
    if rule:
        return rule.points if rule.active else 0
    configured = getattr(settings, 'GAMIFICATION_POINTS', {})
    return int(configured.get(action, DEFAULT_POINT_RULES[action]))


def daily_limit(action):
    from blog.models import GamificationPointRule

    rule = GamificationPointRule.objects.filter(action=action).first()
    if rule:
        return rule.daily_limit if rule.active else 0
    if action == 'comment':
        return int(getattr(settings, 'GAMIFICATION_COMMENT_DAILY_LIMIT', 5))
    return DEFAULT_DAILY_LIMITS.get(action)
