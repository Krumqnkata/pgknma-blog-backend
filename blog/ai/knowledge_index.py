"""Knowledge index of the assistant: every piece of public site content as
searchable chunks (KnowledgeChunk), with embeddings for semantic search.

Sources:
- the database: articles, events, timetable, active poll, approved songs,
  changelog, terms of use, privacy policy, games, levels, point rules, badges;
- the frontend: ``site-knowledge.json`` (FAQ, rules, contacts, team, page map…),
  built by ``npm run build`` from ``Frontend/src/content``;
- ``knowledge.md``: a few core facts that are true regardless of the above.

``sync_knowledge()`` is idempotent and cheap: only new or changed chunks get a
new embedding. It runs from ``run_ai_tasks``.
"""

import hashlib
import json
import logging
import re
from array import array
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import requests
from django.conf import settings
from django.utils import timezone

from blog.models import (
    BellSongSuggestion,
    Changelog,
    GamificationLevel,
    KnowledgeChunk,
    PollQuestion,
    PrivacyPolicy,
    ScheduleEntry,
    SiteSettings,
    TermsOfService,
)
from blog.public_content import public_events_queryset, public_posts_queryset

from .client import EMBEDDING_MODEL, AIQuotaExceeded, AIUnavailable, run_embeddings
from .summaries import markdown_to_text
from .text import stems

logger = logging.getLogger(__name__)

CORE_FILE = Path(__file__).with_name('knowledge.md')
CHUNK_CHARS = 1100
EMBED_BATCH = 24
SITE_KNOWLEDGE_MAX_BYTES = 2_000_000
DAY_LABELS = (('monday', 'Понеделник'), ('tuesday', 'Вторник'), ('wednesday', 'Сряда'), ('thursday', 'Четвъртък'), ('friday', 'Петък'))


@dataclass
class Piece:
    source_type: str
    source_id: str
    title: str
    url: str
    text: str
    part: int = 0


def _split(text, limit=CHUNK_CHARS):
    """Split long text on paragraph, then sentence boundaries."""
    text = re.sub(r'\n{3,}', '\n\n', (text or '').strip())
    if len(text) <= limit:
        return [text] if text else []
    parts, current = [], ''
    for paragraph in re.split(r'\n\s*\n', text):
        sentences = [paragraph] if len(paragraph) <= limit else re.split(r'(?<=[.!?])\s+', paragraph)
        for sentence in sentences:
            if len(current) + len(sentence) + 2 > limit and current:
                parts.append(current.strip())
                current = ''
            current += sentence + '\n\n'
    if current.strip():
        parts.append(current.strip())
    return parts


def _pieces(source_type, source_id, title, url, text):
    return [
        Piece(source_type, str(source_id), title if index == 0 else f'{title} (продължение)', url, chunk, index)
        for index, chunk in enumerate(_split(text))
    ]


def _date(value):
    return timezone.localtime(value).strftime('%d.%m.%Y %H:%M') if value else ''


# ------------------------------------------------------------------ sources

def _posts():
    pieces = []
    for post in public_posts_queryset().prefetch_related('categories'):
        categories = ', '.join(category.full_name for category in post.categories.all())
        head = f'Статия „{post.title}“, публикувана на {_date(post.visible_at)[:10]}.'
        if categories:
            head += f' Категории: {categories}.'
        body = '\n\n'.join(filter(None, [head, post.hook, post.ai_summary, markdown_to_text(post.content)]))
        pieces.extend(_pieces('post', post.pk, post.title, f'/post/{post.pk}', body))
    return pieces


def _events():
    since = timezone.now() - timedelta(days=180)
    pieces = []
    for event in public_events_queryset().filter(start_datetime__gte=since):
        end = f' до {_date(event.end_datetime)}' if event.end_datetime else ''
        text = (
            f'Събитие „{event.title}“ ({event.get_category_display()}): от {_date(event.start_datetime)}{end}, '
            f'място: {event.location}. За кого: {event.attendees_text}.\n\n{markdown_to_text(event.description)}'
        )
        pieces.extend(_pieces('event', event.pk, event.title, f'/events?event={event.pk}', text))
    return pieces


def _schedule(site_settings):
    if not site_settings.enable_program_page:
        return []
    by_class = {}
    for entry in ScheduleEntry.objects.order_by('class_name', 'lesson_number'):
        by_class.setdefault(entry.class_name, []).append(entry)
    pieces = []
    for class_name, entries in by_class.items():
        lines = [f'Седмична програма на {class_name} клас:']
        for field_name, label in DAY_LABELS:
            subjects = [f'{entry.lesson_number}. {getattr(entry, field_name)}' for entry in entries if getattr(entry, field_name)]
            if subjects:
                lines.append(f'{label}: ' + '; '.join(subjects))
        pieces.append(Piece('schedule', class_name, f'Програма на {class_name}', '/program', '\n'.join(lines)))
    return pieces


def _poll(site_settings):
    if not site_settings.enable_weekly_poll:
        return []
    now = timezone.now()
    active = PollQuestion.objects.filter(start_date__lte=now, end_date__gt=now).order_by('-start_date').first()
    if not active:
        return []
    options = '; '.join(option.text for option in active.options.order_by('key'))
    text = f'Активна седмична анкета „{active.title}“ до {_date(active.end_date)}. {active.subtitle or ""} Възможни отговори: {options}.'
    return [Piece('poll', active.pk, f'Анкета: {active.title}', '/weekly-poll', text)]


def _songs(site_settings):
    if not site_settings.enable_bell_suggestions:
        return []
    top = BellSongSuggestion.objects.filter(status='approved').order_by('-votes', '-submitted_at')[:20]
    lines = [f'{song.title} ({song.get_slot_display()}, {song.votes} гласа)' for song in top]
    if not lines:
        return []
    return [Piece('songs', 'approved', 'Одобрени песни за звънеца', '/bell-suggest#approved-songs',
                  'Одобрени предложения за песни за училищния звънец: ' + '; '.join(lines) + '.')]


def _changelog():
    pieces = []
    for entry in Changelog.objects.filter(is_active=True).order_by('-updated_at')[:10]:
        title = f'Промени по сайта от {_date(entry.updated_at)[:10]}'
        pieces.extend(_pieces('changelog', entry.pk, title, '/changelog', markdown_to_text(entry.content)))
    return pieces


def _legal(model, source_type, label, url):
    document = model.objects.order_by('-date').first()
    if not document:
        return []
    pieces = []
    sections = re.split(r'^#{2,3}\s+', document.content or '', flags=re.MULTILINE)
    for index, section in enumerate(sections):
        heading, _, body = section.partition('\n')
        heading = heading.strip().lstrip('#').strip() if index else label
        text = markdown_to_text(body if index else section)
        if text:
            pieces.extend(_pieces(source_type, f'{index}', f'{label} — {heading}' if index else label, url, text))
    return pieces


def _games():
    from blog.games.definitions import GAMES
    from blog.games.services import settings_row

    config = settings_row()
    if not config.enabled:
        return []
    return [
        Piece('game', slug, f'Игра „{title}“', f'/games/{slug}',
              f'Миниигра „{title}“ в раздел „Игри“: {rule} Дава точки за всяка нова решена задача (общо до {config.daily_points_cap} точки от игрите на ден).')
        for slug, (title, rule, _icon) in GAMES.items()
    ]


def _gamification():
    from blog.gamification.catalog import public_point_rules
    from blog.models import Badge

    pieces = []
    levels = [f'ниво {level.number} „{level.title}“ от {level.minimum_points} т.' for level in GamificationLevel.objects.order_by('minimum_points')]
    if levels:
        pieces.append(Piece('gamification', 'levels', 'Нива', '/gamification', 'Нивата в геймификацията: ' + '; '.join(levels) + '.'))
    rules = [
        f'{rule["title"]} — {rule["points"]} т.' + (f' (до {rule["daily_limit"]} на ден)' if rule['daily_limit'] else '')
        for rule in public_point_rules() if rule['active']
    ]
    if rules:
        pieces.append(Piece('gamification', 'points', 'Как се печелят точки', '/gamification', 'Точки се печелят за: ' + '; '.join(rules) + '.'))
    now = timezone.now()
    badges = [
        f'„{badge.title}“ — {badge.description}'
        for badge in Badge.objects.filter(active=True, hidden=False).order_by('title')
        if (not badge.starts_at or badge.starts_at <= now) and (not badge.ends_at or badge.ends_at > now)
    ]
    if badges:
        pieces.extend(_pieces('gamification', 'badges', 'Значки', '/gamification', 'Значки, които могат да се спечелят: ' + '; '.join(badges) + '.'))
    return pieces


def _core():
    """knowledge.md: '## ' sections with facts that are always true."""
    if not CORE_FILE.exists():
        return []
    text = re.sub(r'<!--.*?-->', '', CORE_FILE.read_text(encoding='utf-8'), flags=re.DOTALL)
    pieces = []
    for index, block in enumerate(re.split(r'^## ', text, flags=re.MULTILINE)[1:]):
        heading, _, body = block.partition('\n')
        url_match = re.search(r'^URL:\s*(\S+)\s*$', body, flags=re.MULTILINE)
        url = url_match.group(1) if url_match else ''
        body = re.sub(r'^URL:.*$', '', body, flags=re.MULTILINE).strip()
        pieces.extend(_pieces('core', index, heading.strip(), url, body))
    return pieces


def load_site_knowledge():
    """Sections from the frontend's site-knowledge.json, or None if unavailable."""
    raw = None
    path = getattr(settings, 'AI_SITE_KNOWLEDGE_PATH', '')
    try:
        if path:
            file_path = Path(path)
            if file_path.stat().st_size > SITE_KNOWLEDGE_MAX_BYTES:
                raise ValueError('site-knowledge.json is too large')
            raw = file_path.read_text(encoding='utf-8')
        else:
            url = getattr(settings, 'AI_SITE_KNOWLEDGE_URL', '') or f'{settings.PUBLIC_SITE_URL}/site-knowledge.json'
            response = requests.get(url, timeout=(5, 20), headers={'Accept': 'application/json'})
            response.raise_for_status()
            if len(response.content) > SITE_KNOWLEDGE_MAX_BYTES:
                raise ValueError('site-knowledge.json is too large')
            raw = response.text
        data = json.loads(raw)
    except (OSError, ValueError, requests.RequestException) as exc:
        logger.warning('Could not load site-knowledge.json: %s', exc)
        return None
    sections = data.get('sections') if isinstance(data, dict) else None
    if not isinstance(sections, list):
        return None
    pieces = []
    for section in sections[:500]:
        if not isinstance(section, dict):
            continue
        section_id = str(section.get('id') or '')[:60]
        title = str(section.get('title') or '')[:200]
        url = str(section.get('url') or '')[:300]
        text = str(section.get('text') or '')[:20000]
        if not section_id or not title or not text or not (url.startswith('/') or url.startswith('https://') or not url):
            continue
        pieces.extend(_pieces('page', section_id, title, url, text))
    return pieces


# ------------------------------------------------------------------ sync

def _hash(piece):
    return hashlib.sha256(f'{piece.title}\n{piece.url}\n{piece.text}'.encode('utf-8')).hexdigest()


def pack(vector):
    return array('f', (float(value) for value in vector)).tobytes()


def unpack(data):
    values = array('f')
    values.frombytes(bytes(data))
    return values


def collect_pieces(site_settings=None):
    """All pieces that should be in the index, and the source types that were (re)built."""
    site_settings = site_settings or SiteSettings.objects.filter(pk=1).first() or SiteSettings()
    pieces = [
        *_core(), *_posts(), *_events(), *_schedule(site_settings), *_poll(site_settings),
        *_songs(site_settings), *_changelog(), *_games(), *_gamification(),
        *_legal(TermsOfService, 'terms', 'Условия за ползване', '/terms-of-service'),
        *_legal(PrivacyPolicy, 'privacy', 'Политика за поверителност', '/privacy-policy'),
    ]
    rebuilt = {'core', 'post', 'event', 'schedule', 'poll', 'songs', 'changelog', 'game', 'gamification', 'terms', 'privacy'}
    site_pieces = load_site_knowledge()
    if site_pieces is not None:
        pieces.extend(site_pieces)
        rebuilt.add('page')
    return pieces, rebuilt


def sync_knowledge(*, embed=True, site_settings=None):
    """Bring KnowledgeChunk in line with the site. Returns counters.

    AIQuotaExceeded from the embeddings stops embedding but keeps the text index.
    """
    pieces, rebuilt = collect_pieces(site_settings)
    wanted = {(piece.source_type, piece.source_id, piece.part): piece for piece in pieces}
    stats = {'created': 0, 'updated': 0, 'deleted': 0, 'embedded': 0}

    existing = {
        (chunk.source_type, chunk.source_id, chunk.part): chunk
        for chunk in KnowledgeChunk.objects.filter(source_type__in=rebuilt)
    }
    stale = [chunk.pk for key, chunk in existing.items() if key not in wanted]
    if stale:
        stats['deleted'] = KnowledgeChunk.objects.filter(pk__in=stale).delete()[0]

    for key, piece in wanted.items():
        digest = _hash(piece)
        chunk = existing.get(key)
        stem_text = ' '.join(stems(f'{piece.title} {piece.text}'))
        if chunk is None:
            KnowledgeChunk.objects.create(
                source_type=piece.source_type, source_id=piece.source_id, part=piece.part,
                title=piece.title[:255], url=piece.url[:300], text=piece.text, stems=stem_text,
                content_hash=digest,
            )
            stats['created'] += 1
        elif chunk.content_hash != digest:
            KnowledgeChunk.objects.filter(pk=chunk.pk).update(
                title=piece.title[:255], url=piece.url[:300], text=piece.text, stems=stem_text,
                content_hash=digest, embedding=None, embedding_model='', updated_at=timezone.now(),
            )
            stats['updated'] += 1

    if embed:
        stats['embedded'] = embed_missing()
    return stats


def embed_missing(limit=600):
    """Embed chunks without a current embedding. Returns how many were embedded."""
    todo = list(
        KnowledgeChunk.objects.exclude(embedding_model=EMBEDDING_MODEL)
        .order_by('pk').values_list('pk', 'title', 'text')[:limit]
    )
    done = 0
    for start in range(0, len(todo), EMBED_BATCH):
        batch = todo[start:start + EMBED_BATCH]
        try:
            vectors = run_embeddings([f'{title}\n{text}' for _pk, title, text in batch])
        except AIQuotaExceeded:
            raise
        except AIUnavailable as exc:
            logger.warning('Embeddings failed: %s', exc)
            break
        for (pk, _title, _text), vector in zip(batch, vectors):
            KnowledgeChunk.objects.filter(pk=pk).update(embedding=pack(vector), embedding_model=EMBEDDING_MODEL)
            done += 1
    return done
