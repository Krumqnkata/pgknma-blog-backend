"""Bulgarian date formats for the admin.

Django's own Bulgarian locale has no date-and-time format, so lists fell back
to the American "Ноем. 15, 2026, 9:58 преди обяд". The ISO formats stay as the
second choice, so older input (CSV imports, saved links) keeps working.
"""

DATE_FORMAT = 'd.m.Y'
TIME_FORMAT = 'H:i'
DATETIME_FORMAT = 'd.m.Y H:i'
YEAR_MONTH_FORMAT = 'F Y'
MONTH_DAY_FORMAT = 'j F'
SHORT_DATE_FORMAT = 'd.m.Y'
SHORT_DATETIME_FORMAT = 'd.m.Y H:i'
FIRST_DAY_OF_WEEK = 1  # Monday

DATE_INPUT_FORMATS = [
    '%d.%m.%Y',  # 15.11.2026
    '%Y-%m-%d',  # 2026-11-15
]
TIME_INPUT_FORMATS = [
    '%H:%M',  # 09:58
    '%H:%M:%S',  # 09:58:59
    '%H:%M:%S.%f',
]
DATETIME_INPUT_FORMATS = [
    '%d.%m.%Y %H:%M',
    '%d.%m.%Y %H:%M:%S',
    '%d.%m.%Y',
    '%Y-%m-%d %H:%M:%S',
    '%Y-%m-%d %H:%M:%S.%f',
    '%Y-%m-%d %H:%M',
    '%Y-%m-%d',
]
DECIMAL_SEPARATOR = ','
THOUSAND_SEPARATOR = '\xa0'  # non-breaking space
NUMBER_GROUPING = 3
