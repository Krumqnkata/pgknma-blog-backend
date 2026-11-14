"""Built-in rules: questions the assistant answers straight from the database.

Each rule looks at the question and either returns a RuleAnswer or None.
They run before the AI, cost nothing and are always up to date.
"""

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from django.db.models import Q
from django.utils import timezone

from blog.models import (
    BellSongSuggestion,
    Changelog,
    KnowledgeChunk,
    MemeOfWeek,
    PollQuestion,
    ScheduleEntry,
    UserBadge,
    UserProfile,
    meme_week_start,
)
from blog.public_content import public_events_queryset, public_posts_queryset

from .summaries import markdown_to_text
from .text import WEEKDAY_NAMES, contains_stem, find_class, find_day, find_lesson, is_personal, normalize, stems

SCHOOL_SITE = 'https://pgknma.com/'
ADMISSION_URL = 'https://pgknma.com/priem'
DAY_FIELDS = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday')


@dataclass
class RuleAnswer:
    text: str
    key: str
    sources: list = field(default_factory=list)


@dataclass
class AskContext:
    question: str
    user: object = None
    history: list = field(default_factory=list)
    site_settings: object = None
    today: object = None

    def __post_init__(self):
        self.today = self.today or timezone.localdate()
        self.words = normalize(self.question).split()
        self.stems = stems(self.question)

    @property
    def logged_in(self):
        return bool(self.user and getattr(self.user, 'is_authenticated', False))

    def has(self, *wanted):
        return any(contains_stem(self.stems, stem) for stem in wanted)

    def has_word(self, *wanted):
        return any(word in self.words for word in wanted)

    def previous_user_questions(self):
        return [turn['content'] for turn in self.history if turn.get('role') == 'user']


def _date(value):
    return value.strftime('%d.%m.%Y')


def _datetime(value):
    return timezone.localtime(value).strftime('%d.%m.%Y %H:%M')


def _source(title, url):
    return {'title': title, 'url': url}


# ---------------------------------------------------------------- small talk

_GREETINGS = {'здравей', 'здравейте', 'здрасти', 'здрасте', 'здр', 'хей', 'привет', 'хай', 'хело', 'хелоу', 'ало', 'ехо'}
_GREETING_FILLERS = {'добро', 'добър', 'утро', 'ден', 'вечер', 'асистент', 'асистенте', 'бот', 'там'}
_THANKS = {'благодаря', 'мерси', 'мерсии', 'сенкс', 'тенкс', 'благодарско', 'мерсаж'}


def greeting(ctx):
    words = set(ctx.words)
    if not words or len(ctx.words) > 5:
        return None
    is_greeting = bool(words & _GREETINGS) or {'добро', 'утро'} <= words or {'добър', 'ден'} <= words or {'добър', 'вечер'} <= words
    if is_greeting and words <= (_GREETINGS | _GREETING_FILLERS):
        name = f', {ctx.user.username}' if ctx.logged_in else ''
        return RuleAnswer(
            f'Здравейте{name}! 👋 С какво мога да помогна? Питайте ме за програмата, събитията, '
            'новините, точките и мисиите или как работи нещо в сайта.',
            'greeting',
        )
    return None


def thanks(ctx):
    if set(ctx.words) & _THANKS and len(ctx.words) <= 6:
        return RuleAnswer('Моля! 😊 Ако имате още въпроси, питайте.', 'thanks')
    return None


def help_rule(ctx):
    text = ' '.join(ctx.words)
    triggers = ('какво можеш', 'какво умееш', 'как работиш', 'с какво помагаш', 'какво знаеш', 'какво правиш', 'помощ')
    if any(trigger in text for trigger in triggers) and len(ctx.words) <= 6:
        return RuleAnswer(
            'Мога да отговарям на въпроси за всичко в сайта, например:\n'
            '• програмата — „какво имаме в сряда 11б“, „кой е 3-ти час утре 9в“;\n'
            '• събитията и ваканциите — „какво има утре“, „кога е ваканцията“;\n'
            '• новините, анкетата, мемето на седмицата и песните за звънеца;\n'
            '• точките, нивото, мисиите и значките ви (когато сте влезли);\n'
            '• как се прави нещо в сайта — регистрация, предложения, известия.\n'
            'Ако не знам нещо, ще ви насоча къде да питате.',
            'help',
        )
    return None


# ---------------------------------------------------------------- about the site

def about_site(ctx):
    asks_admission = ctx.has('прием', 'кандидат', 'кандидатст', 'записван')
    asks_official = ctx.has('официал') and (ctx.has('сайт', 'блог', 'училищ', 'гимназ') or ctx.has_word('това'))
    asks_school_site = ctx.has('сайт') and ctx.has('училищ', 'гимназ') and not ctx.has('програм', 'събит')
    if not (asks_admission or asks_official or asks_school_site):
        return None
    text = (
        'PGKNMA.space е официалният блог на ПГКНМА „Проф. Минко Балкански“ — тук са новините, '
        'събитията, програмата и общностните инициативи на училището. Уебсайтът на гимназията е '
        'pgknma.com — там са приемът, документите и административната информация.'
    )
    sources = [_source('Сайт на гимназията', SCHOOL_SITE)]
    if asks_admission:
        text += ' Информацията за приема е на pgknma.com/priem.'
        sources.insert(0, _source('Прием', ADMISSION_URL))
    return RuleAnswer(text, 'about_site', sources)


# ---------------------------------------------------------------- timetable

def _schedule_class(ctx):
    found = find_class(ctx.question)
    if found:
        return found
    for question in reversed(ctx.previous_user_questions()):
        found = find_class(question)
        if found:
            return found
    return None


def _school_day(day):
    """Weekend → the next Monday."""
    if day.weekday() >= 5:
        return day + timedelta(days=7 - day.weekday()), True
    return day, False


def schedule(ctx):
    class_in_question = find_class(ctx.question)
    day = find_day(ctx.question, ctx.today)
    lesson = find_lesson(ctx.question)
    asks = (
        ctx.has('програм', 'предмет', 'разпис')
        or lesson is not None
        or (class_in_question and (day is not None or ctx.has('час', 'учим') or ctx.has_word('имаме')))
        or (ctx.has_word('имаме') and day is not None)
    )
    # A short follow-up like „а в четвъртък?“ after a timetable question.
    if not asks and day is not None and len(ctx.words) <= 4 and _schedule_class(ctx):
        asks = True
    if not asks:
        return None

    sources = [_source('Програма', '/program')]
    if ctx.site_settings is not None and not ctx.site_settings.enable_program_page:
        return RuleAnswer('Страницата „Програма“ в момента е изключена, затова не мога да покажа разписанието.', 'schedule')
    class_name = _schedule_class(ctx)
    if not class_name:
        return RuleAnswer('За кой клас? Напишете например „програма 11б в сряда“.', 'schedule', sources)
    entries = list(ScheduleEntry.objects.filter(class_name__iexact=class_name).order_by('lesson_number'))
    if not entries:
        return RuleAnswer(f'Няма въведена програма за {class_name} клас.', 'schedule', sources)

    requested_day = day or ctx.today
    school_day, moved = _school_day(requested_day)
    field_name = DAY_FIELDS[school_day.weekday()]
    day_name = WEEKDAY_NAMES[school_day.weekday()]
    prefix = 'В събота и неделя няма часове. ' if moved and day is not None else ''

    if lesson is not None:
        entry = next((item for item in entries if item.lesson_number == lesson), None)
        subject = (getattr(entry, field_name) or '').strip() if entry else ''
        if not subject:
            return RuleAnswer(f'{prefix}{class_name} няма {lesson}. час в {day_name}.', 'schedule', sources)
        return RuleAnswer(f'{prefix}{class_name}, {day_name} ({_date(school_day)}), {lesson}. час: {subject}.', 'schedule', sources)

    lines = [
        f'{entry.lesson_number}. час — {getattr(entry, field_name).strip()}'
        for entry in entries if (getattr(entry, field_name) or '').strip()
    ]
    if not lines:
        return RuleAnswer(f'{prefix}{class_name} няма часове в {day_name}.', 'schedule', sources)
    heading = f'{prefix}Програма на {class_name} за {day_name} ({_date(school_day)}):'
    return RuleAnswer('\n'.join([heading, *lines]), 'schedule', sources)


# ---------------------------------------------------------------- calendar

def _event_line(event):
    end = f' – {_datetime(event.end_datetime)}' if event.end_datetime else ''
    place = f', {event.location}' if event.location else ''
    return f'• {event.title} — {_datetime(event.start_datetime)}{end}{place}'


def holidays(ctx):
    if not ctx.has('ваканц', 'празни', 'почивн', 'неучебн'):
        return None
    now = timezone.now()
    upcoming = list(
        public_events_queryset().filter(category='Holiday')
        .filter(Q(start_datetime__gte=now) | Q(end_datetime__gte=now))
        .order_by('start_datetime')[:4]
    )
    sources = [_source('Училищен календар', '/events')]
    if not upcoming:
        return RuleAnswer(
            'В училищния календар няма въведени предстоящи ваканции или празници. '
            'Официалният график на ваканциите е на сайта на гимназията pgknma.com.',
            'holidays',
            [*sources, _source('Сайт на гимназията', SCHOOL_SITE)],
        )
    return RuleAnswer('\n'.join(['Предстоящи празници и ваканции:', *map(_event_line, upcoming)]), 'holidays', sources)


def _week_range(ctx):
    """„тази седмица“ / „следващата седмица“ → (first day, last day, label), else None."""
    if not ctx.has_word('седмица', 'седмицата'):
        return None
    monday = ctx.today - timedelta(days=ctx.today.weekday())
    if ctx.has_word('следващата', 'другата', 'идната', 'следваща'):
        start = monday + timedelta(days=7)
        return start, start + timedelta(days=6), 'следващата седмица'
    return ctx.today, monday + timedelta(days=6), 'тази седмица'


def events(ctx):
    day = find_day(ctx.question, ctx.today)
    week = _week_range(ctx)
    asks = ctx.has('събит', 'мероприят', 'календар', 'предсто') or (
        (day is not None or week is not None) and ctx.has_word('има', 'става', 'ще', 'какво') and not find_class(ctx.question)
    )
    if not asks:
        return None
    sources = [_source('Училищен календар', '/events')]
    queryset = public_events_queryset()
    if week is not None and day is None:
        first, last, label = week
        start = timezone.make_aware(datetime.combine(first, time.min))
        end = timezone.make_aware(datetime.combine(last + timedelta(days=1), time.min))
        in_week = list(
            queryset.filter(start_datetime__lt=end)
            .filter(Q(start_datetime__gte=start) | Q(end_datetime__gte=start))
            .order_by('start_datetime')[:8]
        )
        period = f'{label} ({_date(first)} – {_date(last)})'
        if in_week:
            return RuleAnswer('\n'.join([f'Събития {period}:', *map(_event_line, in_week)]), 'events_week', sources)
        return RuleAnswer(f'Няма събития в календара за {period}.', 'events_week', sources)
    if day is not None:
        start = timezone.make_aware(datetime.combine(day, time.min))
        end = start + timedelta(days=1)
        on_day = list(
            queryset.filter(start_datetime__lt=end)
            .filter(Q(start_datetime__gte=start) | Q(end_datetime__gte=start))
            .order_by('start_datetime')[:6]
        )
        if on_day:
            return RuleAnswer('\n'.join([f'Събития на {_date(day)}:', *map(_event_line, on_day)]), 'events_on_day', sources)
        nxt = queryset.filter(start_datetime__gte=end).order_by('start_datetime').first()
        text = f'На {_date(day)} няма събития в календара.'
        if nxt:
            text += f' Следващото е „{nxt.title}“ на {_datetime(nxt.start_datetime)}.'
        return RuleAnswer(text, 'events_on_day', sources)
    upcoming = list(queryset.filter(start_datetime__gte=timezone.now()).order_by('start_datetime')[:5])
    if not upcoming:
        return RuleAnswer('В момента няма предстоящи събития в училищния календар.', 'events_upcoming', sources)
    return RuleAnswer('\n'.join(['Предстоящи събития:', *map(_event_line, upcoming)]), 'events_upcoming', sources)


# ---------------------------------------------------------------- content

def latest_news(ctx):
    asks_news = ctx.has('новин') or (ctx.has('стат', 'публикац') and ctx.has('последн', 'нов'))
    if not asks_news or ctx.has('сайт') and ctx.has('промен'):
        return None
    posts = list(public_posts_queryset()[:3])
    if not posts:
        return RuleAnswer('Все още няма публикувани новини.', 'latest_news', [_source('Новини', '/news')])
    lines = ['Последните новини:']
    sources = []
    for post in posts:
        summary = (post.ai_summary or post.hook or '').strip()
        if len(summary) > 180:
            summary = summary[:180].rsplit(' ', 1)[0] + '…'
        lines.append(f'• {post.title} ({_date(post.visible_at)}) — {summary}')
        sources.append(_source(post.title, f'/post/{post.pk}'))
    return RuleAnswer('\n'.join(lines), 'latest_news', [*sources, _source('Всички новини', '/news')])


def poll(ctx):
    if not ctx.has('анкет'):
        return None
    sources = [_source('Седмична анкета', '/weekly-poll')]
    if ctx.site_settings is not None and not ctx.site_settings.enable_weekly_poll:
        return RuleAnswer('Седмичната анкета в момента е изключена.', 'poll')
    now = timezone.now()
    active = PollQuestion.objects.filter(start_date__lte=now, end_date__gt=now).order_by('-start_date').first()
    if not active:
        return RuleAnswer('В момента няма активна анкета. Следете известията — новите анкети се обявяват там.', 'poll', sources)
    lines = [f'Активната анкета е „{active.title}“.']
    if active.subtitle:
        lines.append(active.subtitle)
    options = [option.text for option in active.options.order_by('key')]
    if options:
        lines.append('Възможни отговори: ' + '; '.join(options) + '.')
    lines.append(f'Можете да отговорите до {_datetime(active.end_date)}. Влезте в профила си и отворете страницата на анкетата.')
    return RuleAnswer('\n'.join(lines), 'poll', sources)


def memes(ctx):
    if not ctx.has('мем'):
        return None
    sources = [_source('Меме на седмицата', '/meme-of-the-week')]
    if ctx.site_settings is not None and not ctx.site_settings.enable_meme_of_the_week:
        return RuleAnswer('„Меме на седмицата“ в момента е изключено.', 'memes')
    from blog.meme_weeks import latest_winner_result, week_end

    week = meme_week_start(ctx.today)
    leaders = list(
        MemeOfWeek.objects.filter(status='approved', week_start=week)
        .select_related('user').order_by('-votes', 'created_at', 'pk')[:3]
    )
    lines = []
    if leaders:
        lines.append('Тази седмица водят:')
        lines.extend(
            f'• „{meme.title}“ от {meme.user.username if meme.user else "изтрит потребител"} — {meme.votes} гласа'
            for meme in leaders
        )
    else:
        lines.append('Тази седмица все още няма одобрени мемета — качете първото!')
    lines.append(f'Гласуването приключва в неделя в 23:59 ({_date(timezone.localtime(week_end(week)))}).')
    result = latest_winner_result()
    if result:
        author = result.winner.user.username if result.winner.user else 'изтрит потребител'
        lines.append(f'Миналото „Меме на седмицата“ е „{result.winner.title}“ от {author} с {result.winner_votes} гласа.')
    return RuleAnswer('\n'.join(lines), 'memes', sources)


def songs(ctx):
    asks = ctx.has('песн', 'музик') or (ctx.has('звънец') and ctx.has('предлож', 'песн', 'музик'))
    if not asks:
        return None
    sources = [_source('Предложения за звънец', '/bell-suggest')]
    if ctx.site_settings is not None and not ctx.site_settings.enable_bell_suggestions:
        return RuleAnswer('Предложенията за песни за звънеца в момента са изключени.', 'songs')
    top = list(BellSongSuggestion.objects.filter(status='approved').order_by('-votes', '-submitted_at')[:5])
    lines = []
    if top:
        lines.append('Най-харесваните одобрени песни:')
        lines.extend(f'• {song.title} — {song.votes} гласа ({song.get_slot_display()})' for song in top)
    else:
        lines.append('Все още няма одобрени песни.')
    lines.append(
        'За да предложите песен, влезте в профила си, отворете „Предложения за звънец“, поставете линк '
        '(например от YouTube) и изберете за кой момент е.'
    )
    return RuleAnswer('\n'.join(lines), 'songs', sources)


def changelog(ctx):
    asks = ctx.has('промен', 'обновлен', 'обновяван', 'чейнджлог', 'чангелог') and ctx.has('сайт', 'нов', 'последн')
    asks = asks or (ctx.has('нов') and ctx.has('сайт') and not ctx.has('новин'))
    if not asks:
        return None
    entry = Changelog.objects.filter(is_active=True).order_by('-updated_at').first()
    sources = [_source('Промени по сайта', '/changelog')]
    if not entry:
        return RuleAnswer('Все още няма записани промени по сайта.', 'changelog', sources)
    text = markdown_to_text(entry.content)
    if len(text) > 700:
        text = text[:700].rsplit(' ', 1)[0] + '…'
    return RuleAnswer(f'Последните промени ({_date(timezone.localtime(entry.updated_at))}):\n{text}', 'changelog', sources)


def contact(ctx):
    if not ctx.has('телефон', 'адрес', 'имейл', 'мейл', 'контакт', 'свърж', 'поща', 'email'):
        return None
    chunk = KnowledgeChunk.objects.filter(source_type='page', source_id='contact').order_by('part').first()
    if not chunk:
        return None  # The AI can still answer from other knowledge.
    return RuleAnswer(chunk.text, 'contact', [_source('Контакт', '/contact')])


# ---------------------------------------------------------------- games & points

def games(ctx):
    if not ctx.has('игр') or is_personal(ctx.question):
        return None
    from blog.games.definitions import GAMES
    from blog.games.services import settings_row

    config = settings_row()
    if not config.enabled or not config.start_date or config.start_date > ctx.today:
        return RuleAnswer('Игрите в момента не са отворени.', 'games', [_source('Игри', '/games')])
    lines = ['В „Игри“ има шест миниигри:']
    lines.extend(f'• {icon} {title} — {rule}' for title, rule, icon in GAMES.values())
    lines.append(f'За всяка нова решена задача получавате точки, до {config.daily_points_cap} точки от игрите на ден.')
    return RuleAnswer('\n'.join(lines), 'games', [_source('Игри', '/games')])


def gamification_rules(ctx):
    if not ctx.has('точк') or not ctx.has('печел', 'получ', 'взим', 'взем', 'дава', 'трупа', 'събира'):
        return None
    if is_personal(ctx.question) and not ctx.has('печел', 'получ', 'трупа', 'събира'):
        return None
    from blog.gamification.catalog import public_point_rules

    lines = ['Точки се печелят за:']
    for rule in public_point_rules():
        if not rule['active']:
            continue
        limit = f' (до {rule["daily_limit"]} пъти на ден)' if rule['daily_limit'] else ''
        lines.append(f'• {rule["title"]} — {rule["points"]} т.{limit}')
    lines.append('С точките се вдига нивото и се отключват значки и награди.')
    return RuleAnswer('\n'.join(lines), 'gamification_rules', [_source('Мисии и награди', '/gamification')])


def leaderboard(ctx):
    if not (ctx.has('класац') or (ctx.has('първ', 'топ', 'водещ') and ctx.has('точк', 'потребител'))):
        return None
    rows = list(
        UserProfile.objects.filter(leaderboard_opt_in=True, gamification_points__gt=0)
        .select_related('user').order_by('-gamification_points', 'user__username')[:5]
    )
    sources = [_source('Класация', '/gamification#leaderboard')]
    if not rows:
        return RuleAnswer('В класацията все още няма участници. Участието се включва от настройките на профила.', 'leaderboard', sources)
    lines = ['Класацията за всички времена:']
    lines.extend(f'{index}. {row.user.username} — {row.gamification_points} т.' for index, row in enumerate(rows, start=1))
    lines.append('В класацията участват само потребителите, които са я включили.')
    return RuleAnswer('\n'.join(lines), 'leaderboard', sources)


def personal_stats(ctx):
    """Points, level, missions and badges of the logged-in user."""
    wants_points = ctx.has('точк', 'нив')
    wants_missions = ctx.has('мис')
    wants_badges = ctx.has('значк', 'наград')
    if not (is_personal(ctx.question) and (wants_points or wants_missions or wants_badges)):
        return None
    sources = [_source('Мисии и награди', '/gamification')]
    if not ctx.logged_in:
        return RuleAnswer('Влезте в профила си, за да видите своите точки, ниво, мисии и значки.', 'personal_login', [_source('Вход', '/login')])
    from blog.gamification.services import active_missions_for_user, level_state

    profile, _ = UserProfile.objects.get_or_create(user=ctx.user)
    lines = []
    if wants_points or not (wants_missions or wants_badges):
        state = level_state(profile.gamification_points)
        line = f'Имате {profile.gamification_points} точки'
        if state.current:
            line += f' и сте ниво {state.current.number} „{state.current.title}“'
        line += '.'
        if state.next:
            line += f' До ниво {state.next.number} „{state.next.title}“ ви остават {state.points_to_next} точки.'
        lines.append(line)
    if wants_missions:
        missions = active_missions_for_user(ctx.user)
        if not missions:
            lines.append('В момента нямате активни мисии.')
        for mission in missions:
            status = 'изпълнена ✅' if mission['completed_at'] else f'{mission["progress_percent"]}%'
            lines.append(f'Мисия „{mission["title"]}“ ({mission["reward_points"]} т.) — {status}:')
            lines.extend(
                f'• {req["label"]}: {req["current"]}/{req["target"]}' + (' ✓' if req['complete'] else '')
                for req in mission['requirements']
            )
    if wants_badges:
        earned = list(UserBadge.objects.filter(user=ctx.user).select_related('badge').order_by('-awarded_at'))
        if earned:
            titles = ', '.join(f'„{row.badge.title}“' for row in earned[:8])
            lines.append(f'Имате {len(earned)} значки: {titles}.')
        else:
            lines.append('Все още нямате значки.')
    return RuleAnswer('\n'.join(lines), 'personal_stats', sources)


# The order matters: more specific rules first.
BUILTIN_RULES = (
    greeting,
    thanks,
    help_rule,
    about_site,
    personal_stats,
    schedule,
    holidays,
    events,
    poll,
    memes,
    songs,
    changelog,
    latest_news,
    contact,
    gamification_rules,
    leaderboard,
    games,
)


def match_builtin(ctx):
    for rule in BUILTIN_RULES:
        answer = rule(ctx)
        if answer is not None:
            return answer
    return None
