from django.db import migrations

# (name, kind, start "d.m", end "d.m", greeting, effect)
YEARLY = (
    ('Зима', 'season', '1.12', '29.2', '', 'snow'),
    ('Пролет', 'season', '1.3', '31.5', '', 'petals'),
    ('Лято', 'season', '1.6', '31.8', '', 'none'),
    ('Есен', 'season', '1.9', '30.11', '', 'leaves'),
    ('Нова година', 'holiday', '31.12', '2.1', 'Честита Нова година!', 'stars'),
    ('Св. Валентин и Трифон Зарезан', 'holiday', '14.2', '14.2', 'Честит 14 февруари!', 'hearts'),
    ('Годишнина от гибелта на Васил Левски', 'holiday', '19.2', '19.2', 'Поклон пред Апостола!', 'none'),
    ('Баба Марта', 'holiday', '1.3', '1.3', 'Честита Баба Марта!', 'martenitsa'),
    ('Ден на Освобождението', 'holiday', '3.3', '3.3', 'Честит 3 март!', 'tricolor'),
    ('Международен ден на жената', 'holiday', '8.3', '8.3', 'Честит 8 март!', 'petals'),
    ('Гергьовден', 'holiday', '6.5', '6.5', 'Честит Гергьовден!', 'none'),
    ('Ден на българската просвета и култура', 'holiday', '24.5', '24.5', 'Честит 24 май!', 'letters'),
    ('Ден на детето', 'holiday', '1.6', '1.6', 'Честит Ден на детето!', 'balloons'),
    ('Ден на Ботев и на загиналите за свободата на България', 'holiday', '2.6', '2.6', 'Поклон пред героите!', 'none'),
    ('Патронен празник – проф. Минко Балкански', 'holiday', '24.7', '24.7', 'Честит патронен празник!', 'confetti'),
    ('Ден на Съединението', 'holiday', '6.9', '6.9', 'Честит 6 септември!', 'tricolor'),
    ('Първи учебен ден', 'holiday', '15.9', '15.9', 'Успешна учебна година!', 'balloons'),
    ('Ден на Независимостта', 'holiday', '22.9', '22.9', 'Честит 22 септември!', 'tricolor'),
    ('Хелоуин', 'holiday', '31.10', '31.10', 'Честит Хелоуин!', 'bats'),
    ('Ден на народните будители', 'holiday', '1.11', '1.11', 'Честит Ден на народните будители!', 'letters'),
    ('Коледа', 'holiday', '24.12', '26.12', 'Весела Коледа!', 'snow'),
)


def seed(apps, schema_editor):
    HeaderLogo = apps.get_model('blog', 'HeaderLogo')
    if HeaderLogo.objects.exists():
        return
    logos = []
    for name, kind, start, end, greeting, effect in YEARLY:
        start_day, start_month = map(int, start.split('.'))
        end_day, end_month = map(int, end.split('.'))
        logos.append(HeaderLogo(
            name=name,
            kind=kind,
            date_mode='yearly',
            start_day=start_day,
            start_month=start_month,
            end_day=end_day,
            end_month=end_month,
            greeting=greeting,
            effect=effect,
        ))
    logos.append(HeaderLogo(
        name='Великден',
        kind='holiday',
        date_mode='easter',
        easter_offset_start=-1,
        easter_offset_end=1,
        greeting='Христос воскресе!',
        effect='eggs',
    ))
    HeaderLogo.objects.bulk_create(logos)


def unseed(apps, schema_editor):
    HeaderLogo = apps.get_model('blog', 'HeaderLogo')
    HeaderLogo.objects.filter(image='').delete()


class Migration(migrations.Migration):
    dependencies = [
        ('blog', '0079_header_logo'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
