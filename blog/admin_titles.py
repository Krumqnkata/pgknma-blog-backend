"""Bulgarian page titles and „+ Нов …“ labels for every admin screen.

Django builds titles like „Изберете Публикация за промяна“ and „Добави
Публикация“. These helpers give „Публикации“ and „Нова публикация“ instead,
from the model's own verbose names, so a new model needs nothing extra.
"""

import re

from django.utils.text import capfirst

_CYRILLIC_WORD = re.compile(r'[а-яёА-ЯЁ]+')


def _is_cyrillic_word(word):
    return bool(_CYRILLIC_WORD.fullmatch(word))


def _head_word(name):
    """The first ordinary Bulgarian word, skipping „ИИ“ or „Push“."""
    for word in name.split():
        if _is_cyrillic_word(word) and not word.isupper():
            return word.lower()
    return ''


def _new_word(opts):
    name = str(opts.verbose_name)
    if name == str(opts.verbose_name_plural):
        return 'Нови'  # „Настройки“ is the same in both forms
    word = _head_word(name)
    if word.endswith(('ски', 'шки', 'цки')):
        return 'Нов'  # adjective: „Потребителски профил“
    if word.endswith(('а', 'я', 'ост', 'ест')):
        return 'Нова'  # „-ост“: „активност“
    if word.endswith(('о', 'е')):
        return 'Ново'
    return 'Нов'


def lower_noun(name):
    """„Публикация“ -> „публикация“; „ИИ чернова“ and „Push абонамент“ stay."""
    name = str(name)
    first = name.split(' ', 1)[0]
    if _is_cyrillic_word(first) and first[1:] == first[1:].lower():
        return name[:1].lower() + name[1:]
    return name


def add_label(opts):
    """„Нова публикация“, „Ново събитие“, „Нов коментар“."""
    return f'{_new_word(opts)} {lower_noun(opts.verbose_name)}'


def list_title(opts):
    """„Публикации“."""
    return capfirst(str(opts.verbose_name_plural))


def item_title(opts):
    """„Публикация“ (the record's own name is shown before it)."""
    return capfirst(str(opts.verbose_name))
