"""Compile locale/bg/LC_MESSAGES/django.po without GNU gettext.

The server and Windows machines often have no `msgfmt`, so this small
compiler writes the .mo file in pure Python. Only simple (non-plural)
entries are supported, which is all the admin translation file uses.
"""

import ast
import struct
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


def parse_po(text):
    """Return {msgid: msgstr}. Plural entries are not supported."""
    messages = {}
    msgid = msgstr = None
    target = None

    def flush():
        if msgid is not None and msgstr is not None:
            messages[msgid] = msgstr

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('msgid_plural') or line.startswith('msgstr['):
            raise CommandError('Plural entries are not supported in this file.')
        if line.startswith('msgid '):
            flush()
            msgid, msgstr, target = ast.literal_eval(line[6:]), None, 'id'
        elif line.startswith('msgstr '):
            msgstr, target = ast.literal_eval(line[7:]), 'str'
        elif line.startswith('"'):
            value = ast.literal_eval(line)
            if target == 'id':
                msgid += value
            elif target == 'str':
                msgstr += value
    flush()
    return messages


def write_mo(messages, path):
    keys = sorted(messages)
    ids = b''
    strs = b''
    offsets = []
    for key in keys:
        key_bytes = key.encode('utf-8')
        value_bytes = messages[key].encode('utf-8')
        offsets.append((len(ids), len(key_bytes), len(strs), len(value_bytes)))
        ids += key_bytes + b'\0'
        strs += value_bytes + b'\0'
    count = len(keys)
    key_table_start = 7 * 4
    value_table_start = key_table_start + count * 8
    ids_start = value_table_start + count * 8
    strs_start = ids_start + len(ids)
    key_table = b''.join(struct.pack('<2I', length, ids_start + start) for start, length, _, _ in offsets)
    value_table = b''.join(struct.pack('<2I', length, strs_start + start) for _, _, start, length in offsets)
    header = struct.pack('<7I', 0x950412DE, 0, count, key_table_start, value_table_start, 0, 0)
    path.write_bytes(header + key_table + value_table + ids + strs)


class Command(BaseCommand):
    help = 'Компилира българския превод на админ панела (locale/bg) без GNU gettext.'

    # django.po: page texts; djangojs.po: texts in the admin JavaScript.
    DOMAINS = ('django', 'djangojs')

    def handle(self, *args, **options):
        folder = Path(settings.BASE_DIR) / 'locale' / 'bg' / 'LC_MESSAGES'
        main_po = folder / 'django.po'
        if not main_po.exists():
            raise CommandError(f'Липсва {main_po}')
        for domain in self.DOMAINS:
            po_path = folder / f'{domain}.po'
            if not po_path.exists():
                continue
            messages = parse_po(po_path.read_text(encoding='utf-8'))
            write_mo(messages, po_path.with_suffix('.mo'))
            self.stdout.write(self.style.SUCCESS(f'Готово ({domain}): {len(messages) - 1} превода.'))
