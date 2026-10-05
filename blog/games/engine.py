"""Pure game rules. Only public_state() may cross the API boundary."""
from collections import Counter
from copy import deepcopy
import re
import unicodedata


class InvalidMove(ValueError):
    pass


def normalize(text):
    return ' '.join(unicodedata.normalize('NFC', str(text)).strip().casefold().split())


def initial_state(game):
    return {
        'quiz': {'answers': []}, 'word': {'letters': []},
        'code': {'guesses': []}, 'robot': {'attempts': []},
        'memory': {'matched': [], 'open': [], 'turns': 0},
        'hunt': {'answers': []},
    }[game]


def integer(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise InvalidMove('Невалиден избор.')
    return value


def simulate_robot(puzzle, commands):
    x, y = puzzle['start']
    direction = puzzle['direction']
    path = [[x, y]]
    for command in commands:
        if command == 'left':
            direction = (direction - 1) % 4
        elif command == 'right':
            direction = (direction + 1) % 4
        elif command == 'forward':
            dx, dy = ((0, -1), (1, 0), (0, 1), (-1, 0))[direction]
            x, y = x + dx, y + dy
            path.append([x, y])
            if not (0 <= x < 6 and 0 <= y < 6) or [x, y] in puzzle['walls']:
                return False, path
        else:
            return False, path
    return [x, y] == puzzle['goal'], path


def move(game, puzzle, original, action):
    state = deepcopy(original)
    status, score = 'active', None
    if game == 'quiz':
        index = len(state['answers'])
        choice = integer(action.get('choice'), 0, 3)
        state['answers'].append(choice)
        if len(state['answers']) == 5:
            score = sum(a == q['answer'] for a, q in zip(state['answers'], puzzle['questions']))
            status = 'won' if score >= 4 else 'lost'
    elif game == 'word':
        letter = normalize(action.get('letter', '')).upper()
        if not re.fullmatch('[А-Я]', letter):
            raise InvalidMove('Избери една българска буква.')
        if letter in state['letters']:
            raise InvalidMove('Тази буква вече е избрана.')
        state['letters'].append(letter)
        secret = puzzle['word'].upper()
        mistakes = sum(letter not in secret for letter in state['letters'])
        if set(secret) <= set(state['letters']):
            status, score = 'won', mistakes
        elif mistakes >= 6:
            status, score = 'lost', mistakes
    elif game == 'code':
        guess = action.get('guess', '')
        if not isinstance(guess, str) or not re.fullmatch('[0-9]{4}', guess) or len(set(guess)) != 4:
            raise InvalidMove('Въведи четири различни цифри.')
        if any(row['guess'] == guess for row in state['guesses']):
            raise InvalidMove('Тази комбинация вече е проверена.')
        secret = puzzle['secret']
        exact = sum(a == b for a, b in zip(guess, secret))
        state['guesses'].append({'guess': guess, 'exact': exact, 'misplaced': len(set(guess) & set(secret)) - exact})
        if exact == 4:
            status, score = 'won', len(state['guesses'])
        elif len(state['guesses']) == 8:
            status, score = 'lost', 8
    elif game == 'robot':
        order = action.get('order')
        if not isinstance(order, list) or len(order) != len(puzzle['commands']) or any(type(i) is not int for i in order) or sorted(order) != list(range(len(order))):
            raise InvalidMove('Използвай всяка дадена команда точно веднъж.')
        commands = [puzzle['commands'][i] for i in order]
        won, path = simulate_robot(puzzle, commands)
        state['attempts'].append({'order': order, 'path': path, 'won': won})
        if won or len(state['attempts']) == 5:
            status, score = ('won' if won else 'lost'), len(state['attempts'])
    elif game == 'memory':
        if action.get('kind') == 'close':
            if len(state['open']) != 2:
                raise InvalidMove('Няма двойка за затваряне.')
            state['open'] = []
        else:
            index = integer(action.get('index'), 0, 15)
            if len(state['open']) == 2 or index in state['matched'] or index in state['open']:
                raise InvalidMove('Избери друга затворена карта или затвори отворената двойка.')
            state['open'].append(index)
            if len(state['open']) == 2:
                state['turns'] += 1
                a, b = state['open']
                if puzzle['cards'][a] == puzzle['cards'][b]:
                    state['matched'].extend((a, b))
                    state['open'] = []
                if len(state['matched']) == 16 or state['turns'] == 20:
                    status = 'won' if len(state['matched']) == 16 else 'lost'
                    score = state['turns']
    elif game == 'hunt':
        answer = action.get('answer', '')
        if not isinstance(answer, str) or not 1 <= len(answer.strip()) <= 120:
            raise InvalidMove('Въведи кратък отговор до 120 знака.')
        state['answers'].append(answer.strip())
        won = normalize(answer) in {normalize(a) for a in puzzle['answers']}
        if won or len(state['answers']) == 3:
            status, score = ('won' if won else 'lost'), len(state['answers'])
    else:
        raise InvalidMove('Непозната игра.')
    return state, status, score


def public_state(game, puzzle, state, status):
    finished = status in ('won', 'lost')
    if game == 'quiz':
        index = len(state['answers'])
        data = {'answered': index, 'total': 5}
        if status == 'active':
            q = puzzle['questions'][index]
            data['question'] = {'text': q['text'], 'options': q['options']}
        if finished:
            data['review'] = [{**q, 'chosen': a} for q, a in zip(puzzle['questions'], state['answers'])]
        return data
    if game == 'word':
        secret = puzzle['word'].upper()
        return {'hint': puzzle['hint'], 'letters': state['letters'], 'remaining': 6 - sum(c not in secret for c in state['letters']), 'masked': [c if c in state['letters'] or finished else '_' for c in secret]}
    if game == 'code':
        return {'guesses': state['guesses'], 'remaining': 8 - len(state['guesses']), **({'solution': puzzle['secret']} if finished else {})}
    if game == 'robot':
        return {key: puzzle[key] for key in ('start', 'goal', 'walls', 'commands', 'direction')} | {'attempts': state['attempts'], 'remaining': 5 - len(state['attempts'])}
    if game == 'memory':
        visible = set(state['matched'] + state['open'])
        return {**state, 'cards': [card if i in visible or finished else None for i, card in enumerate(puzzle['cards'])], 'remaining': 20 - state['turns']}
    return {'title': puzzle['title'], 'text': puzzle['text'], 'question': puzzle['question'], 'answers': state['answers'], 'remaining': 3 - len(state['answers']), **({'solution': puzzle['answers'][0]} if finished else {})}


def validate_puzzle(game, puzzle):
    if game == 'quiz':
        assert len(puzzle['questions']) == 5
        for q in puzzle['questions']:
            assert q['text'] and len(q['options']) == len(set(q['options'])) == 4
            assert type(q['answer']) is int and 0 <= q['answer'] < 4
            assert q['explanation'] and q['source']
    elif game == 'word':
        assert re.fullmatch('[А-Яа-я]{3,20}', puzzle['word']) and puzzle['hint']
    elif game == 'code':
        assert re.fullmatch('[0-9]{4}', puzzle['secret']) and len(set(puzzle['secret'])) == 4
    elif game == 'robot':
        assert Counter(puzzle['solution']) == Counter(puzzle['commands'])
        assert simulate_robot(puzzle, puzzle['solution'])[0]
        assert puzzle['start'] != puzzle['goal'] and len(puzzle['commands']) <= 20
    elif game == 'memory':
        assert len(puzzle['cards']) == 16 and list(Counter(puzzle['cards']).values()) == [2] * 8
    elif game == 'hunt':
        assert puzzle['title'] and puzzle['question'] and puzzle['answers']
        assert all(normalize(a) in normalize(puzzle['text']) for a in puzzle['answers'])
