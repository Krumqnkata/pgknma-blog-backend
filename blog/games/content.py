"""Deterministic, versioned content preparation. Never called on a play request."""
import hashlib
import itertools
import json
import random
from functools import lru_cache

from .definitions import DAYS, GAMES, PACK_VERSION
from .engine import normalize, validate_puzzle
from .facts import CAPITALS, CHEM_SOURCE, ELEMENT_NAMES, ELEMENT_SYMBOLS, GEO_SOURCE, PYTHON_SOURCE, TECH, TECH_SOURCE, pairs
from .word_bank import words


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def question(rng, text, answer, alternatives, explanation, source):
    answer = str(answer)
    others = sorted({str(item) for item in alternatives} - {answer})
    options = [answer, *rng.sample(others, 3)]
    rng.shuffle(options)
    return {'text': text, 'options': options, 'answer': options.index(answer), 'explanation': explanation, 'source': source}


def factual_questions(rng):
    result = []
    tech = pairs(TECH)
    assert len(tech) == 100
    for term, definition in tech:
        source = PYTHON_SOURCE if tech.index([term, definition]) >= 78 else TECH_SOURCE
        result.extend((
            question(rng, f'Кой термин отговаря на описанието: „{definition}“?', term, [p[0] for p in tech], f'{term}: {definition}.', source),
            question(rng, f'Кое описание се отнася за „{term}“?', definition, [p[1] for p in tech], f'{term}: {definition}.', source),
        ))
    capitals = pairs(CAPITALS)
    assert len(capitals) == 100
    for country, capital in capitals:
        explanation = f'{capital} е столицата на {country}.'
        result.extend((
            question(rng, f'Коя е столицата на {country}?', capital, [p[1] for p in capitals], explanation, GEO_SOURCE),
            question(rng, f'На коя държава е столица град {capital}?', country, [p[0] for p in capitals], explanation, GEO_SOURCE),
        ))
    assert len(ELEMENT_NAMES) == len(ELEMENT_SYMBOLS) == 100
    for number, (name, symbol) in enumerate(zip(ELEMENT_NAMES, ELEMENT_SYMBOLS), 1):
        explanation = f'Елементът {name} има символ {symbol} и атомен номер {number}.'
        result.extend((
            question(rng, f'Кой е химичният символ на {name}?', symbol, ELEMENT_SYMBOLS, explanation, CHEM_SOURCE),
            question(rng, f'Кой химичен елемент се означава с {symbol}?', name, ELEMENT_NAMES, explanation, CHEM_SOURCE),
            question(rng, f'Кой е атомният номер на {name}?', number, range(1, 101), explanation, CHEM_SOURCE),
            question(rng, f'Кой химичен елемент има атомен номер {number}?', name, ELEMENT_NAMES, explanation, CHEM_SOURCE),
        ))
    rng.shuffle(result)
    assert len(result) == 800
    return result


def math_question(rng, index):
    # Each family uses disjoint parameter ranges in daily and practice packs.
    n, kind = divmod(index, 12)
    a, b = n + 4, n % 9 + 3
    if kind == 0:
        text, answer, why = f'Колко е {a} + {b}?', a + b, 'Събираме двете числа.'
    elif kind == 1:
        text, answer, why = f'Колко е {a + b} − {b}?', a, 'Изваждаме второто число от първото.'
    elif kind == 2:
        text, answer, why = f'Колко е {a} × {b}?', a * b, 'Умножаваме двата множителя.'
    elif kind == 3:
        text, answer, why = f'Колко е {a * b} ÷ {b}?', a, 'Делението е обратната операция на умножението.'
    elif kind == 4:
        text, answer, why = f'Какъв е периметърът на правоъгълник със страни {a} и {b} cm?', 2 * (a + b), 'Периметърът е два пъти сбора на съседните страни.'
    elif kind == 5:
        text, answer, why = f'Какво е лицето на квадрат със страна {a} cm (в cm²)?', a * a, 'Лицето на квадрат е страната, умножена по себе си.'
    elif kind == 6:
        text, answer, why = f'Кое е следващото число: {a}, {a + b}, {a + 2*b}, {a + 3*b}, …?', a + 4*b, f'Всеки път прибавяме {b}.'
    elif kind == 7:
        text, answer, why = f'Колко е 25% от {4*a}?', a, '25% са една четвърт.'
    elif kind == 8:
        text, answer, why = f'Колко бита съдържат {a} байта?', 8*a, 'Един байт съдържа осем бита.'
    elif kind == 9:
        text, answer, why = f'Колко пъти се изпълнява тялото на цикъл for i in range({a}) в Python?', a, 'range(n) дава n цели числа от 0 до n−1.'
    elif kind == 10:
        text, answer, why = f'Колко е средното аритметично на {a}, {a+2} и {a+4}?', a+2, 'Събираме трите числа и делим на три.'
    else:
        text, answer, why = f'Какъв е остатъкът при деление на {a*b + b-1} на {b}?', b-1, f'{a*b + b-1} = {a} × {b} + {b-1}.'
    alternatives = [answer + i for i in (-5, -3, -2, -1, 1, 2, 3, 5, 10) if answer+i >= 0]
    return question(rng, text, answer, alternatives, f'{why} Отговор: {answer}.', 'https://docs.python.org/3/tutorial/introduction.html#numbers')


def robot_puzzle(rng):
    # Build a simple path first, then put obstacles only outside its cells.
    for _ in range(200):
        start = (rng.randrange(6), rng.randrange(6))
        path = [start]
        for _ in range(rng.randint(6, 10)):
            x, y = path[-1]
            options = [(x+dx, y+dy) for dx, dy in ((0,-1),(1,0),(0,1),(-1,0)) if 0 <= x+dx < 6 and 0 <= y+dy < 6 and (x+dx,y+dy) not in path]
            if not options:
                break
            path.append(rng.choice(options))
        if len(path) < 6:
            continue
        direction = rng.randrange(4)
        facing = direction
        commands = []
        for (x,y), (nx,ny) in zip(path, path[1:]):
            wanted = ((0,-1),(1,0),(0,1),(-1,0)).index((nx-x,ny-y))
            delta = (wanted-facing) % 4
            commands.extend(['left'] if delta == 3 else ['right']*delta)
            commands.append('forward')
            facing = wanted
        if len(commands) > 20:
            continue
        shuffled = commands[:]
        rng.shuffle(shuffled)
        free = [(x,y) for x in range(6) for y in range(6) if (x,y) not in path]
        return {'start': list(start), 'goal': list(path[-1]), 'direction': direction, 'walls': [list(p) for p in rng.sample(free, 8)], 'commands': shuffled, 'solution': commands}
    raise ValueError('Cannot generate a solvable robot board')


def hunt_puzzles():
    # Original fictional experiments. Facts about the experiment are explicitly
    # in its text; no dependency on actual school news or real pupils.
    topics = [
        ('Сортиране', 'сортиране', 'Подреждането по нарастващ размер улеснява сравняването на резултатите.'),
        ('Търсене', 'линейно търсене', 'При линейното търсене разглеждаме елементите последователно до намиране на търсения.'),
        ('Средна стойност', 'средно аритметично', 'Средното аритметично се получава, като сборът на измерванията се раздели на броя им.'),
        ('Проверка на данни', 'контролна сума', 'Контролната сума помага да се открият някои промени в данните, но сама по себе си не доказва тяхната автентичност.'),
        ('Модел на маршрут', 'граф', 'Графът представя обекти като върхове, а връзките между тях като ребра.'),
        ('Отчитане на време', 'хронометър', 'Измерването на продължителност позволява да се сравняват различни решения на една задача.'),
        ('Работа с таблица', 'таблица', 'Редовете могат да представят отделни наблюдения, а колоните — техните свойства.'),
        ('Визуализация', 'диаграма', 'Диаграмата може да направи тенденциите по-лесни за забелязване, ако мащабът е ясно означен.'),
        ('Резервно копие', 'архив', 'Резервното копие помага при загуба на оригинала. Възстановяването също трябва да се проверява.'),
        ('Проверка на програма', 'тест', 'Тестът сравнява действителния резултат с очаквания за избран пример. Един успешен тест не доказва липса на всички грешки.'),
    ]
    devices = ['сензор', 'робот', 'микроконтролер', 'термометър', 'компютър']
    labels = ['клен', 'бор', 'дъб', 'бук', 'ясен', 'липа', 'ела', 'орех', 'бреза', 'върба']
    result = []
    for i in range(100):
        name, method, lesson = topics[i % 10]
        device, label = devices[i % 5], labels[i // 10]
        count, minutes = 12+i, 5+i*2
        title = f'{name}: опит {i+1}'
        text = (f'Това е учебен, измислен експеримент, който показва как се води дневник на проект. '
                f'Екипът озаглавява работата си „{title}“. За основен инструмент избира {device}. '
                f'В дневника са записани {count} наблюдения, а продължителността на работата е {minutes} минути. '
                f'За да различат този набор от други, участниците му дават етикет „{label}“. '
                f'Ключовото понятие в урока е „{method}“. {lesson} '
                'Преди да сравняват резултатите, участниците проверяват дали всички записи имат еднакви мерни единици. '
                'След това друг член на екипа прочита дневника и опитва да възстанови стъпките. '
                'Ясните описания правят експеримента разбираем дори за човек, който не е присъствал.')
        questions = [
            ('Какъв е основният инструмент?', device),
            ('Колко наблюдения са записани? Напиши само числото.', str(count)),
            ('Колко минути продължава работата? Напиши само числото.', str(minutes)),
            ('Какъв е етикетът на набора?', label),
            ('Кое е ключовото понятие в урока?', method),
        ]
        result.extend({'title': title, 'text': text, 'question': f'В „{title}“: {q}', 'answers': [a]} for q, a in questions)
    return result


@lru_cache(maxsize=1)
def build_pack():
    rng = random.Random(PACK_VERSION)
    facts = factual_questions(rng)
    generated = [math_question(rng, i) for i in range(1700)]
    word_list = words()
    rng.shuffle(word_list)
    assert len(word_list) >= 600
    codes = [''.join(chars) for chars in itertools.permutations('0123456789', 4)]
    rng.shuffle(codes)
    hunts = hunt_puzzles()
    daily_hunts, practice_hunts = hunts[:400], hunts[400:]
    rng.shuffle(daily_hunts)
    rng.shuffle(practice_hunts)
    pack = []
    icons = list('🍎🍋🍇🍒🥝🥕🌻🌵🌲🍁🐱🐶🦊🐻🐼🐸🦋🐝🐢🐬🚀🚲🚂🚁⚽🏀🎲🎯🎹🎸🔑💎')
    for game in GAMES:
        for pool, size in (('daily', DAYS), ('practice', {'quiz':100,'word':200,'hunt':100}.get(game,400))):
            for slot in range(size):
                offset = slot + (DAYS if pool == 'practice' else 0)
                if game == 'quiz':
                    qs = facts[slot*2:slot*2+2] + generated[slot*3:slot*3+3] if pool == 'daily' else generated[1200+slot*5:1205+slot*5]
                    qs = qs[:]
                    rng.shuffle(qs)
                    payload = {'questions': qs}
                elif game == 'word':
                    payload = word_list[offset]
                elif game == 'code':
                    payload = {'secret': codes[offset]}
                elif game == 'robot':
                    payload = robot_puzzle(rng)
                elif game == 'memory':
                    cards = rng.sample(icons, 8)*2
                    rng.shuffle(cards)
                    payload = {'cards': cards}
                else:
                    payload = (daily_hunts if pool == 'daily' else practice_hunts)[slot]
                validate_puzzle(game, payload)
                pack.append({'key': f'{PACK_VERSION}:{game}:{pool}:{slot}', 'version': PACK_VERSION, 'game': game, 'pool': pool, 'slot': slot, 'payload': payload, 'fingerprint': digest([game, payload])})
    errors = validate_rows(pack)
    if errors:
        raise ValueError('; '.join(errors))
    return pack


def validate_rows(rows):
    errors = []
    fingerprints, questions, secrets, words_seen = set(), set(), set(), set()
    slots = {}
    for row in rows:
        game, payload = row['game'], row['payload']
        try:
            validate_puzzle(game, payload)
            actual = digest([game, payload])
            assert actual == row['fingerprint'] and actual not in fingerprints
            fingerprints.add(actual)
            slots.setdefault((game, row['pool']), set()).add(row['slot'])
            if game == 'quiz':
                for q in payload['questions']:
                    key = normalize(q['text'])
                    assert key not in questions
                    questions.add(key)
            if game == 'word':
                assert payload['word'] not in words_seen
                words_seen.add(payload['word'])
            if game == 'code':
                assert payload['secret'] not in secrets
                secrets.add(payload['secret'])
        except (AssertionError, KeyError, TypeError, ValueError) as exc:
            errors.append(f"Невалидна или повторена задача: {row.get('key', game)} ({exc})")
    for game in GAMES:
        if slots.get((game, 'daily')) != set(range(DAYS)):
            errors.append(f'{game}: нужни са точно {DAYS} дневни задачи')
        minimum = {'quiz':100, 'word':200, 'hunt':100}.get(game,400)
        if len(slots.get((game, 'practice'), set())) < minimum:
            errors.append(f'{game}: недостатъчно допълнителни задачи')
    return errors


def validate_database():
    from .models import GamePuzzle
    return validate_rows(GamePuzzle.objects.filter(version=PACK_VERSION, pool__in=('daily','practice')).values('game','pool','slot','payload','fingerprint','key'))
