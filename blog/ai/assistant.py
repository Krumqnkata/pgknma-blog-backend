"""The AI assistant: answers visitors' questions only from the site's content.

Questions are not stored anywhere. Each request finds the relevant FAQ
entries, articles, events and (when asked) a class timetable, and sends only
those to the model, which keeps the request small on the free plan.
"""

import re
from dataclasses import dataclass, field
from datetime import timedelta
from functools import lru_cache
from functools import reduce
from operator import or_
from pathlib import Path

from django.utils import timezone

from blog.models import ScheduleEntry
from blog.public_content import (
    event_search_q,
    post_search_q,
    public_events_queryset,
    public_posts_queryset,
)

from . import config, prompts
from .client import run_chat
from .summaries import markdown_to_text

KNOWLEDGE_FILE = Path(__file__).with_name('knowledge.md')
MAX_KEYWORDS = 6
MAX_FAQ_SECTIONS = 3
MAX_POSTS = 3
MAX_EVENTS = 3
UPCOMING_EVENTS = 3
POST_EXCERPT_CHARS = 700

_WORD = re.compile(r'[0-9a-zа-яѝ]+', re.IGNORECASE)
_CLASS_NAME = re.compile(r'(?<!\d)(8|9|10|11|12)\s*[-–]?\s*([абвгдеa-e])(?![а-яa-z])', re.IGNORECASE)
_LATIN_TO_CYRILLIC_CLASS = {'a': 'а', 'b': 'б', 'v': 'в', 'c': 'в', 'g': 'г', 'd': 'д', 'e': 'е'}

# Very common Bulgarian words that would match almost every text.
STOPWORDS = {
    'като', 'какво', 'каква', 'какъв', 'какви', 'кога', 'къде', 'колко', 'който', 'която', 'което',
    'които', 'защо', 'може', 'мога', 'можем', 'имам', 'имаме', 'има', 'няма', 'тази', 'този', 'това',
    'тези', 'там', 'тук', 'сега', 'още', 'много', 'малко', 'само', 'вече', 'дали', 'ако', 'или',
    'обаче', 'когато', 'моля', 'искам', 'трябва', 'бъде', 'бъдат', 'нещо', 'някой', 'всеки', 'всички',
    'сайт', 'сайта', 'сайтът', 'училище', 'училището', 'днес', 'утре', 'кажи', 'кажете', 'знаеш',
}


@dataclass
class AssistantAnswer:
    answer: str
    sources: list = field(default_factory=list)


def extract_keywords(question):
    """Simple stems: long words cut to 6 letters so „ваканцията“ finds „ваканция“."""
    keywords = []
    for word in _WORD.findall(question.lower()):
        if len(word) < 4 or word in STOPWORDS:
            continue
        stem = word[:6] if len(word) > 6 else word
        if stem not in keywords:
            keywords.append(stem)
    return keywords[:MAX_KEYWORDS]


@lru_cache(maxsize=1)
def knowledge_sections():
    text = KNOWLEDGE_FILE.read_text(encoding='utf-8')
    text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)
    sections = []
    for block in re.split(r'^## ', text, flags=re.MULTILINE)[1:]:
        heading, _, body = block.partition('\n')
        body = re.sub(r'^# .*$', '', body, flags=re.MULTILINE).strip()
        sections.append((heading.strip(), body))
    return tuple(sections)


def _score(text, keywords):
    lowered = text.lower()
    return sum(lowered.count(keyword) for keyword in keywords)


def relevant_knowledge(keywords):
    scored = [
        (_score(f'{heading} {heading} {body}', keywords), heading, body)
        for heading, body in knowledge_sections()
    ]
    scored = [item for item in scored if item[0] > 0]
    scored.sort(key=lambda item: item[0], reverse=True)
    return [(heading, body) for _, heading, body in scored[:MAX_FAQ_SECTIONS]]


def relevant_posts(keywords):
    if not keywords:
        return []
    query = reduce(or_, (post_search_q(keyword) for keyword in keywords))
    candidates = list(public_posts_queryset().filter(query).distinct()[:20])
    candidates.sort(
        key=lambda post: _score(f'{post.title} {post.title} {post.hook} {post.content}', keywords),
        reverse=True,
    )
    return candidates[:MAX_POSTS]


def relevant_events(keywords):
    """Return (events for the context, whether any of them matched the question)."""
    since = timezone.now() - timedelta(days=1)
    upcoming = list(
        public_events_queryset().filter(start_datetime__gte=since).order_by('start_datetime')[:UPCOMING_EVENTS]
    )
    matched = []
    if keywords:
        query = reduce(or_, (event_search_q(keyword) for keyword in keywords))
        matched = list(public_events_queryset().filter(query).order_by('-start_datetime')[:MAX_EVENTS])
    seen, events = set(), []
    for event in matched + upcoming:
        if event.pk not in seen:
            seen.add(event.pk)
            events.append(event)
    return events, bool(matched)


def class_schedule(question, site_settings):
    if not site_settings.enable_program_page:
        return None, []
    match = _CLASS_NAME.search(question)
    if not match:
        return None, []
    letter = match.group(2).lower()
    letter = _LATIN_TO_CYRILLIC_CLASS.get(letter, letter)
    class_name = f'{match.group(1)}{letter.upper()}'
    entries = list(ScheduleEntry.objects.filter(class_name__iexact=class_name).order_by('lesson_number'))
    return class_name, entries


def _format_datetime(value):
    return timezone.localtime(value).strftime('%d.%m.%Y %H:%M') if value else ''


def build_context(question, site_settings=None):
    """Return (context text, sources) for one question."""
    site_settings = site_settings or config.get_site_settings()
    keywords = extract_keywords(question)
    parts, sources = [], []

    knowledge = relevant_knowledge(keywords)
    if knowledge:
        parts.append('Често задавани въпроси и правила:\n' + '\n\n'.join(
            f'- {heading}\n{body}' for heading, body in knowledge
        ))
        sources.append({'title': 'Често задавани въпроси', 'url': '/faq'})

    posts = relevant_posts(keywords)
    if posts:
        blocks = []
        for post in posts:
            excerpt = post.ai_summary or markdown_to_text(post.content)[:POST_EXCERPT_CHARS]
            date = _format_datetime(getattr(post, 'visible_at', None) or post.created_at)
            blocks.append(f'- Статия „{post.title}“ ({date}): {post.hook}\n{excerpt}')
            sources.append({'title': post.title, 'url': f'/post/{post.pk}'})
        parts.append('Статии от сайта:\n' + '\n\n'.join(blocks))

    events, events_matched = relevant_events(keywords)
    if events:
        lines = []
        for event in events:
            end = f' до {_format_datetime(event.end_datetime)}' if event.end_datetime else ''
            description = markdown_to_text(event.description)[:300]
            lines.append(
                f'- {event.title}: {_format_datetime(event.start_datetime)}{end}, {event.location}. '
                f'За: {event.attendees_text}. {description}'
            )
        parts.append(f'Събития (днес е {timezone.localdate().strftime("%d.%m.%Y")}):\n' + '\n'.join(lines))
        # Upcoming events are always in the context, but the calendar is only
        # shown as a source when an event matched the question.
        if events_matched:
            sources.append({'title': 'Училищен календар', 'url': '/events'})

    class_name, entries = class_schedule(question, site_settings)
    if class_name:
        if entries:
            rows = [
                f'{entry.lesson_number}. час — Пн: {entry.monday or "—"}; Вт: {entry.tuesday or "—"}; '
                f'Ср: {entry.wednesday or "—"}; Чт: {entry.thursday or "—"}; Пт: {entry.friday or "—"}'
                for entry in entries
            ]
            parts.append(f'Седмична програма на {class_name} клас:\n' + '\n'.join(rows))
            sources.append({'title': f'Програма на {class_name}', 'url': '/program'})
        else:
            parts.append(f'Няма въведена програма за {class_name} клас.')

    context = '\n\n'.join(parts) if parts else 'Няма намерена информация по този въпрос.'
    return context, sources


def answer_question(question, history=()):
    """Ask the model. Raises AIError subclasses on failure."""
    site_settings = config.get_site_settings()
    context, sources = build_context(question, site_settings)
    messages = [{'role': 'system', 'content': prompts.ASSISTANT_SYSTEM}]
    for turn in history:
        messages.append({'role': turn['role'], 'content': turn['content']})
    messages.append({
        'role': 'user',
        'content': f'КОНТЕКСТ:\n{context}\n\nВЪПРОС:\n{question}',
    })
    result = run_chat(
        model=config.student_text_model(site_settings),
        messages=messages,
        max_tokens=350,
        temperature=0.2,
    )
    return AssistantAnswer(answer=result.text, sources=sources[:5])
