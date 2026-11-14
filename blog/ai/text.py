"""Text helpers for the assistant: normalising Bulgarian (and "шльокавица"),
light stemming and spotting days, classes and lesson numbers."""

import re
from datetime import timedelta

from django.utils import timezone

_WORD = re.compile(r'[0-9a-zа-я]+')
_LATIN = re.compile(r'[a-z]')

# Multi-letter combinations first. "y" is usually "ъ" in шльокавица.
_TRANSLIT_MULTI = (
    ('sht', 'щ'), ('sh', 'ш'), ('ch', 'ч'), ('zh', 'ж'), ('ts', 'ц'),
    ('yu', 'ю'), ('iu', 'ю'), ('ju', 'ю'), ('ya', 'я'), ('ia', 'я'), ('ja', 'я'),
)
_TRANSLIT_SINGLE = {
    'a': 'а', 'b': 'б', 'c': 'ц', 'd': 'д', 'e': 'е', 'f': 'ф', 'g': 'г', 'h': 'х',
    'i': 'и', 'j': 'й', 'k': 'к', 'l': 'л', 'm': 'м', 'n': 'н', 'o': 'о', 'p': 'п',
    'q': 'я', 'r': 'р', 's': 'с', 't': 'т', 'u': 'у', 'v': 'в', 'w': 'в', 'x': 'кс',
    'y': 'ъ', 'z': 'з', '4': 'ч', '6': 'ш',
}

STOPWORDS = {
    'като', 'какво', 'каква', 'какъв', 'какви', 'кога', 'къде', 'колко', 'който', 'която', 'което',
    'които', 'защо', 'може', 'мога', 'можем', 'имам', 'имаме', 'има', 'няма', 'тази', 'този', 'това',
    'тези', 'там', 'тук', 'сега', 'още', 'много', 'малко', 'само', 'вече', 'дали', 'ако', 'или',
    'обаче', 'когато', 'моля', 'искам', 'трябва', 'бъде', 'бъдат', 'нещо', 'някой', 'всеки', 'всички',
    'сайт', 'сайта', 'сайтът', 'кажи', 'кажете', 'знаеш', 'знаете', 'ли', 'да', 'не', 'на', 'за', 'от',
    'по', 'до', 'се', 'си', 'ще', 'са', 'ми', 'ни', 'ти', 'ви', 'го', 'ги', 'му', 'им', 'е', 'и', 'в',
    'с', 'а', 'че', 'но', 'аз', 'ние', 'вие', 'те', 'той', 'тя', 'то', 'при', 'със', 'във', 'ко',
}

# Definite articles, plural and other common endings, longest first.
_SUFFIXES = (
    'иите', 'ията', 'ието', 'ищата', 'ата', 'ото', 'ите', 'ият', 'ета', 'ове', 'ища',
    'ът', 'ят', 'та', 'то', 'те', 'ия', 'ие', 'а', 'я', 'о', 'е', 'и', 'ъ', 'у',
)

WEEKDAYS = {
    'понеделник': 0, 'вторник': 1, 'сряда': 2, 'четвъртък': 3, 'петък': 4, 'събота': 5, 'неделя': 6,
}
WEEKDAY_NAMES = ('понеделник', 'вторник', 'сряда', 'четвъртък', 'петък', 'събота', 'неделя')
_WEEKDAY_STEMS = {'понедел': 0, 'вторн': 1, 'сряд': 2, 'четвърт': 3, 'петък': 4, 'петъ': 4, 'събот': 5, 'недел': 6}

_CLASS_NAME = re.compile(r'(?<!\d)(8|9|10|11|12)\s*[-–]?\s*([абвгде])(?![а-я])')
_LESSON = re.compile(r'(?<!\d)([1-8])\s*[-–]?\s*(?:ви|ри|ти|ми|и)?\s*час')
_LESSON_WORDS = {'първи': 1, 'втори': 2, 'трети': 3, 'четвърти': 4, 'пети': 5, 'шести': 6, 'седми': 7, 'осми': 8}


def _transliterate(word):
    for latin, cyrillic in _TRANSLIT_MULTI:
        word = word.replace(latin, cyrillic)
    return ''.join(_TRANSLIT_SINGLE.get(char, char) for char in word)


def normalize(text):
    """Lower case, Cyrillic only (шльокавица converted), words separated by single spaces."""
    text = (text or '').lower().replace('ѝ', 'и').replace('ё', 'е')
    words = []
    for word in _WORD.findall(text):
        if _LATIN.search(word):
            word = _transliterate(word)
        words.append(word)
    return ' '.join(words)


def stem(word):
    """Very light Bulgarian stemmer: strips up to two common endings."""
    for _ in range(2):
        for suffix in _SUFFIXES:
            if word.endswith(suffix) and len(word) - len(suffix) >= 3:
                word = word[: -len(suffix)]
                break
        else:
            break
    return word[:7]


def stems(text, *, keep_stopwords=False):
    """Ordered, de-duplicated stems of the meaningful words in `text`."""
    result = []
    for word in normalize(text).split():
        if not keep_stopwords and (word in STOPWORDS or len(word) < 3) and not word.isdigit():
            continue
        value = stem(word)
        if value not in result:
            result.append(value)
    return result


def stem_matches(a, b):
    """True when two stems are the same word (allowing a longer/shorter form)."""
    if a == b:
        return True
    if len(a) >= 4 and len(b) >= 4:
        return a.startswith(b) or b.startswith(a)
    return False


def contains_stem(question_stems, wanted):
    return any(stem_matches(value, wanted) for value in question_stems)


def find_class(text):
    """'11б', '11 Б', '11-b' → '11Б'; None if no class is mentioned."""
    match = _CLASS_NAME.search(normalize(text))
    if not match:
        return None
    return f'{match.group(1)}{match.group(2).upper()}'


def find_lesson(text):
    normalized = normalize(text)
    match = _LESSON.search(normalized)
    if match:
        return int(match.group(1))
    for word, number in _LESSON_WORDS.items():
        if re.search(rf'\b{word}\s+час', normalized):
            return number
    return None


def find_day(text, today=None):
    """A date mentioned in the question: днес, утре, вдругиден or a weekday (the next one)."""
    today = today or timezone.localdate()
    words = normalize(text).split()
    if 'вдругиден' in words:
        return today + timedelta(days=2)
    if 'утре' in words:
        return today + timedelta(days=1)
    if 'днес' in words or 'сега' in words:
        return today
    for word in words:
        for prefix, weekday in _WEEKDAY_STEMS.items():
            if word.startswith(prefix):
                delta = (weekday - today.weekday()) % 7
                return today + timedelta(days=delta)
    return None


def is_personal(question):
    """The question is about the asker ("колко точки имам", "моите мисии")."""
    words = set(normalize(question).split())
    return bool(words & {'аз', 'мен', 'мене', 'мой', 'моя', 'моят', 'моите', 'мои', 'имам', 'ми', 'съм', 'моето', 'мое'})
