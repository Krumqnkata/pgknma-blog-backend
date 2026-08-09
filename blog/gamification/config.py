from django.conf import settings


DEFAULT_POINT_RULES = {
    'poll_vote': 5,
    'comment': 5,
    'song_approved': 20,
    'meme_approved': 20,
}

ACTION_DESCRIPTIONS = {
    'poll_vote': 'Гласуване в анкета',
    'comment': 'Валиден коментар',
    'song_approved': 'Одобрено предложение за песен',
    'meme_approved': 'Одобрено меме',
}


def point_value(action):
    configured = getattr(settings, 'GAMIFICATION_POINTS', {})
    return int(configured.get(action, DEFAULT_POINT_RULES[action]))


def rewarded_comments_per_day():
    return int(getattr(settings, 'GAMIFICATION_COMMENT_DAILY_LIMIT', 5))
