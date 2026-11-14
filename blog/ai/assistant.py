"""The AI assistant: rules first, then the AI with the best matching site content.

1. Admin rules (AssistantRule) — ready answers written in the admin.
2. Built-in rules (intents.py) — timetable, events, news, points… from the database.
3. The AI model with the most relevant knowledge chunks (retrieval.py).
Questions nobody can answer are logged without any user data, so admins can
add a rule for them.
"""

import re
from dataclasses import dataclass, field

from django.utils import timezone

from blog.models import UserBadge, UserProfile

from . import config, prompts
from .client import AIQuotaExceeded, AIUnavailable, run_chat
from .intents import AskContext, match_builtin
from .retrieval import build_context, search
from .rules import log_unanswered, match_admin_rule
from .text import is_personal

CONTACT_SOURCE = {'title': 'Контакт', 'url': '/contact'}
FAQ_SOURCE = {'title': 'Често задавани въпроси', 'url': '/faq'}

NO_ANSWER_TEXT = (
    'Не намерих отговор на този въпрос в сайта. Опитайте да попитате по друг начин, '
    'вижте „Често задавани въпроси“ или пишете на екипа чрез страницата за контакт.'
)
RULES_ONLY_TEXT = (
    'Нямам готов отговор на този въпрос. Мога да помогна с програмата, събитията, новините, '
    'анкетата, мемето на седмицата, точките и мисиите. За други въпроси вижте '
    '„Често задавани въпроси“ или пишете на екипа чрез страницата за контакт.'
)
LIMIT_TEXT = (
    'За днес свободните въпроси към ИИ свършиха. Все още мога да отговарям за програмата, '
    'събитията, новините, анкетата, мемето на седмицата и точките — питайте!'
)
AI_DOWN_TEXT = (
    'ИИ в момента не е достъпен, затова мога да отговарям само на въпроси, за които имам '
    'готов отговор — програмата, събитията, новините, точките. Опитайте след малко.'
)
_SAYS_UNKNOWN = re.compile(
    r'не знам|нямам (?:такава |достатъчно )?информация|не намерих|не мога да (?:намеря|отговоря)|няма информация'
)
WEEKDAYS = ('понеделник', 'вторник', 'сряда', 'четвъртък', 'петък', 'събота', 'неделя')


@dataclass
class AssistantResult:
    answer: str
    sources: list = field(default_factory=list)
    answer_source: str = 'none'   # rule | ai | none
    rule_key: str = ''


def personal_context(user):
    """Short summary of the user's own progress, sent to the AI only for personal questions."""
    from blog.gamification.services import active_missions_for_user, level_state

    profile, _ = UserProfile.objects.get_or_create(user=user)
    state = level_state(profile.gamification_points)
    lines = [f'Потребителско име: {user.username}', f'Точки: {profile.gamification_points}']
    if state.current:
        lines.append(f'Ниво: {state.current.number} „{state.current.title}“')
    if state.next:
        lines.append(f'Следващо ниво: {state.next.number} „{state.next.title}“, остават {state.points_to_next} точки')
    for mission in active_missions_for_user(user):
        goals = ', '.join(f'{req["label"]} {req["current"]}/{req["target"]}' for req in mission['requirements'])
        done = 'изпълнена' if mission['completed_at'] else f'{mission["progress_percent"]}%'
        lines.append(f'Мисия „{mission["title"]}“ ({done}): {goals}')
    badges = [row.badge.title for row in UserBadge.objects.filter(user=user).select_related('badge')[:15]]
    lines.append('Значки: ' + (', '.join(badges) if badges else 'няма'))
    return '\n'.join(lines)


def _messages(question, history, context, personal):
    today = timezone.localdate()
    system = f'{prompts.ASSISTANT_SYSTEM}\nДнес е {WEEKDAYS[today.weekday()]}, {today.strftime("%d.%m.%Y")}.'
    messages = [{'role': 'system', 'content': system}]
    for turn in list(history)[-6:]:
        if turn.get('role') in ('user', 'assistant') and turn.get('content'):
            messages.append({'role': turn['role'], 'content': str(turn['content'])[:1000]})
    parts = [f'КОНТЕКСТ:\n{context or "Няма намерена информация."}']
    if personal:
        parts.append(f'ЛИЧНИ ДАННИ НА ПИТАЩИЯ:\n{personal}')
    parts.append(f'ВЪПРОС:\n{question}')
    messages.append({'role': 'user', 'content': '\n\n'.join(parts)})
    return messages


def answer_question(question, *, user=None, history=(), site_settings=None, reserve_ai=None, release_ai=None):
    """Answer one question. `reserve_ai`/`release_ai` guard the site-wide daily AI limit."""
    site_settings = site_settings or config.get_site_settings()
    history = list(history)

    rule = match_admin_rule(question)
    if rule is None:
        rule = match_builtin(AskContext(question, user=user, history=history, site_settings=site_settings))
    if rule is not None:
        return AssistantResult(rule.text, rule.sources, 'rule', rule.key)

    if not config.assistant_ai_enabled(site_settings):
        log_unanswered(question)
        return AssistantResult(RULES_ONLY_TEXT, [FAQ_SOURCE, CONTACT_SOURCE], 'none')

    logged_in = bool(user and user.is_authenticated)
    personal = personal_context(user) if logged_in and is_personal(question) else ''
    hits = search(question)
    if not hits and not personal:
        log_unanswered(question)
        return AssistantResult(NO_ANSWER_TEXT, [FAQ_SOURCE, CONTACT_SOURCE], 'none')

    if reserve_ai is not None and not reserve_ai():
        return AssistantResult(LIMIT_TEXT, [], 'none', 'daily_limit')
    context, sources = build_context(hits)
    try:
        result = run_chat(
            model=config.student_text_model(site_settings),
            messages=_messages(question, history, context, personal),
            max_tokens=450,
            temperature=0.2,
        )
    except AIQuotaExceeded:
        if release_ai:
            release_ai()
        return AssistantResult(LIMIT_TEXT, [], 'none', 'daily_limit')
    except AIUnavailable:
        if release_ai:
            release_ai()
        return AssistantResult(AI_DOWN_TEXT, [], 'none', 'ai_unavailable')

    text = result.text
    if prompts.UNKNOWN_MARKER in text:
        text = text.replace(prompts.UNKNOWN_MARKER, '').strip()
        # Models sometimes add the marker to a good answer; trust it only with a real "I don't know".
        if not text or len(text) < 40 or _SAYS_UNKNOWN.search(text.lower()):
            log_unanswered(question)
            return AssistantResult(text or NO_ANSWER_TEXT, [CONTACT_SOURCE], 'none')
    return AssistantResult(text, sources, 'ai')
