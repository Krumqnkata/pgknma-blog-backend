GENERAL = 'general'
CONTENT_POST = 'content_post'
CONTENT_EVENT = 'content_event'
EVENT_REMINDER = 'event_reminder'
CONTENT_POLL = 'content_poll'
CONTENT_CHANGELOG = 'content_changelog'
COMMENT_REPLY = 'comment_reply'
SUBMISSION_STATUS = 'submission_status'
MISSION_AVAILABLE = 'mission_available'
SECURITY_LOGIN = 'security_login'
SECURITY_ACCOUNT = 'security_account'
TEAM_CHAT_INVITE = 'team_chat_invite'


USER_PREFERENCE_FIELD_BY_CATEGORY = {
    GENERAL: 'general_enabled',
    CONTENT_POST: 'posts_enabled',
    CONTENT_EVENT: 'events_enabled',
    EVENT_REMINDER: 'events_enabled',
    CONTENT_POLL: 'polls_enabled',
    CONTENT_CHANGELOG: 'changelog_enabled',
    COMMENT_REPLY: 'comment_replies_enabled',
    SUBMISSION_STATUS: 'submissions_enabled',
    MISSION_AVAILABLE: 'mission_available_enabled',
    SECURITY_LOGIN: 'security_enabled',
    SECURITY_ACCOUNT: 'security_enabled',
    TEAM_CHAT_INVITE: 'general_enabled',
    'gamification_points': 'points_enabled',
    'gamification_level': 'levels_enabled',
    'gamification_badge': 'badges_enabled',
    'gamification_mission': 'missions_enabled',
    'gamification_reward': 'rewards_enabled',
}

DEVICE_PREFERENCE_FIELD_BY_CATEGORY = {
    GENERAL: 'general_enabled',
    CONTENT_POST: 'posts_enabled',
    CONTENT_EVENT: 'events_enabled',
    EVENT_REMINDER: 'events_enabled',
    CONTENT_POLL: 'polls_enabled',
    CONTENT_CHANGELOG: 'changelog_enabled',
}

PUBLIC_PUSH_CATEGORIES = frozenset(DEVICE_PREFERENCE_FIELD_BY_CATEGORY)


def user_preference_field(category):
    return USER_PREFERENCE_FIELD_BY_CATEGORY.get(category, 'general_enabled')
