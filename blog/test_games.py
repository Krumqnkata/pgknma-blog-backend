import json
import uuid
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date, datetime, timedelta
from io import StringIO
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError as ModelValidationError
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import close_old_connections
from django.test import SimpleTestCase, TestCase, TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone
from rest_framework.test import APIClient

from blog.games.content import build_pack, digest, validate_database, validate_rows
from blog.games.definitions import GAMES
from blog.games.engine import InvalidMove, initial_state, move, public_state, simulate_robot
from blog.games.models import GamePuzzle, GameRequest, GameSession, GameSettings
from blog.games.services import Conflict, day_bounds, get_session, start_session, submit_action
from blog.models import GamificationPointRule, PointTransaction, SiteSettings, UserProfile


def winning_actions(puzzle):
    p = puzzle.payload
    if puzzle.game == 'quiz':
        return [{'choice': q['answer']} for q in p['questions']]
    if puzzle.game == 'word':
        return [{'letter': letter} for letter in sorted(set(p['word'].upper()))]
    if puzzle.game == 'code':
        return [{'guess': p['secret']}]
    if puzzle.game == 'robot':
        remaining = list(range(len(p['commands'])))
        order = []
        for command in p['solution']:
            index = next(i for i in remaining if p['commands'][i] == command)
            order.append(index)
            remaining.remove(index)
        return [{'order': order}]
    if puzzle.game == 'memory':
        return [{'index': i} for card in dict.fromkeys(p['cards']) for i, value in enumerate(p['cards']) if value == card]
    return [{'answer': p['answers'][0]}]


def win(user, session):
    for action in winning_actions(session.puzzle):
        session = submit_action(user, session.pk, uuid.uuid4(), session.revision, action)
    return session


class PackTests(SimpleTestCase):
    def test_full_pack_has_400_days_and_disjoint_reserves(self):
        pack = build_pack()
        self.assertEqual(len(pack), 4000)
        self.assertEqual(validate_rows(pack), [])
        daily_questions = [q for row in pack if row['game'] == 'quiz' and row['pool'] == 'daily' for q in row['payload']['questions']]
        self.assertEqual(len(daily_questions), 2000)
        self.assertEqual(len({q['text'] for q in daily_questions}), 2000)
        hunts = [row['payload'] for row in pack if row['game'] == 'hunt']
        self.assertEqual(len({p['text'] for p in hunts}), 100)
        daily_texts = {r['payload']['text'] for r in pack if r['game'] == 'hunt' and r['pool'] == 'daily'}
        practice_texts = {r['payload']['text'] for r in pack if r['game'] == 'hunt' and r['pool'] == 'practice'}
        self.assertFalse(daily_texts & practice_texts)

    def test_every_robot_witness_reaches_goal(self):
        for row in build_pack():
            if row['game'] == 'robot':
                self.assertTrue(simulate_robot(row['payload'], row['payload']['solution'])[0], row['key'])

    def test_corrupted_pack_is_rejected(self):
        rows = deepcopy(build_pack())
        rows[0]['payload']['questions'][0]['answer'] = 12
        self.assertTrue(validate_rows(rows))

    def test_code_feedback_counts_positions_and_leading_zero(self):
        state, status, _ = move('code', {'secret':'0123'}, initial_state('code'), {'guess':'0219'})
        self.assertEqual(state['guesses'][0], {'guess':'0219','exact':1,'misplaced':2})
        self.assertEqual(status, 'active')

    def test_memory_does_not_reveal_other_cards_and_requires_closing_mismatch(self):
        puzzle = {'cards': list('abcdefgh')*2}
        state, _, _ = move('memory', puzzle, initial_state('memory'), {'index':0})
        self.assertEqual(public_state('memory', puzzle, state, 'active')['cards'], ['a'] + [None]*15)
        state, _, _ = move('memory', puzzle, state, {'index':1})
        with self.assertRaises(InvalidMove):
            move('memory', puzzle, state, {'index':2})
        state, _, _ = move('memory', puzzle, state, {'kind':'close'})
        self.assertEqual(state['turns'], 1)
        self.assertEqual(state['open'], [])

    def test_quiz_requires_four_correct_and_hides_solutions_until_end(self):
        puzzle = next(r['payload'] for r in build_pack() if r['game']=='quiz')
        for correct_count in (3,4):
            state = initial_state('quiz')
            for i,q in enumerate(puzzle['questions']):
                visible = public_state('quiz', puzzle, state, 'active')
                self.assertNotIn('answer', visible['question'])
                self.assertNotIn('review', visible)
                state,status,score = move('quiz', puzzle,state,{'choice': q['answer'] if i < correct_count else (q['answer']+1)%4})
            self.assertEqual(status, 'won' if correct_count == 4 else 'lost')
            self.assertEqual(score, correct_count)

    def test_repeated_letter_and_boolean_choice_are_rejected(self):
        with self.assertRaises(InvalidMove):
            move('word', {'word':'книга'}, {'letters':['К']}, {'letter':'к'})
        with self.assertRaises(InvalidMove):
            move('quiz', {}, {'answers':[]}, {'choice':True})

    def test_memory_loses_on_twentieth_mismatch(self):
        state,status,score = move('memory', {'cards':list('abcdefgh')*2}, {'matched':[],'open':[0],'turns':19}, {'index':1})
        self.assertEqual((status,score), ('lost',20))

    def test_word_loses_after_six_misses_and_hunt_normalizes_case_and_space(self):
        state,status,score = move('word', {'word':'книга'}, {'letters':list('БВЕЖЗ')}, {'letter':'Л'})
        self.assertEqual(status,'lost')
        self.assertEqual(move('hunt', {'answers':['Линеен метод']}, {'answers':[]}, {'answer':'  линеен   МЕТОД  '})[1], 'won')

    def test_day_bounds_follow_sofia_dst(self):
        for day,hours in ((date(2026,3,29),23),(date(2026,10,25),25)):
            start,end = day_bounds(day)
            self.assertEqual((end.timestamp()-start.timestamp())/3600,hours)


class GamesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('prepare_games', start_date=timezone.localdate(), activate=True, stdout=StringIO())
        cls.user = User.objects.create_user(username='player',password='pass')
        cls.other = User.objects.create_user(username='other',password='pass')

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def start(self, game='code', mode='daily'):
        return start_session(self.user, game, mode, uuid.uuid4())

    def test_all_six_games_award_existing_points_then_cap_at_thirty(self):
        for game in GAMES:
            session = win(self.user, self.start(game))
            self.assertEqual((session.status,session.points_awarded),('won',5),game)
        self.assertEqual(UserProfile.objects.get(user=self.user).gamification_points,30)
        session = win(self.user,self.start('code','practice'))
        self.assertEqual(session.points_awarded,0)
        self.assertEqual(PointTransaction.objects.filter(user=self.user,action__startswith='game_').count(),6)

    def test_partial_award_and_configurable_game_rule(self):
        GameSettings.objects.filter(pk=1).update(daily_points_cap=7)
        self.assertEqual(win(self.user,self.start()).points_awarded,5)
        self.assertEqual(win(self.user,self.start('code','practice')).points_awarded,2)
        GamificationPointRule.objects.filter(action='game_word').update(active=False)
        self.assertEqual(win(self.user,self.start('word')).points_awarded,0)

    def test_start_resume_and_move_retry_do_not_duplicate(self):
        key = uuid.uuid4()
        first = start_session(self.user,'code','daily',key)
        self.assertEqual(start_session(self.user,'code','daily',key).pk,first.pk)
        self.assertEqual(self.start().pk,first.pk)
        action_key = uuid.uuid4()
        action = {'guess':first.puzzle.payload['secret']}
        done = submit_action(self.user,first.pk,action_key,0,action)
        retried = submit_action(self.user,first.pk,action_key,0,action)
        self.assertEqual(done.revision,retried.revision)
        self.assertEqual(PointTransaction.objects.filter(user=self.user,action='game_code').count(),1)
        with self.assertRaises(Conflict):
            submit_action(self.user,first.pk,action_key,0,{'guess':'9999'})

    def test_stale_revision_does_not_consume_an_attempt(self):
        first = self.start('word')
        submit_action(self.user,first.pk,uuid.uuid4(),0,{'letter':'А'})
        with self.assertRaises(Conflict):
            submit_action(self.user,first.pk,uuid.uuid4(),0,{'letter':'Б'})
        first.refresh_from_db()
        self.assertEqual(first.state['letters'],['А'])

    def test_replay_never_rewards_and_own_daily_remains_finished(self):
        original = win(self.user,self.start())
        replay = start_session(self.user,'code','replay',uuid.uuid4(),original.puzzle_id)
        self.assertFalse(replay.reward_eligible)
        self.assertEqual(win(self.user,replay).points_awarded,0)
        self.assertEqual(self.start().pk,original.pk)

    def test_expired_daily_cannot_complete_and_next_day_has_new_task(self):
        session = self.start()
        tomorrow = session.expires_at + timedelta(seconds=1)
        with patch('django.utils.timezone.now',return_value=tomorrow):
            done = submit_action(self.user,session.pk,uuid.uuid4(),0,{'guess':session.puzzle.payload['secret']})
            self.assertEqual(done.status,'expired')
            self.assertEqual(done.points_awarded,0)
            self.assertNotEqual(self.start().puzzle_id,session.puzzle_id)

    def test_practice_rewards_on_completion_day_and_survives_midnight(self):
        session = self.start('code','practice')
        GameSession.objects.filter(pk=session.pk).update(expires_at=timezone.now()+timedelta(days=2))
        with patch('django.utils.timezone.now',return_value=day_bounds(timezone.localdate())[1]+timedelta(seconds=1)):
            result = win(self.user,get_session(self.user,session.pk))
            self.assertEqual(result.points_awarded,5)

    def test_api_requires_login_and_hides_other_users_sessions(self):
        session = self.start()
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get('/api/games/').status_code,200)
        self.assertEqual(self.client.post('/api/games/sessions/',{},format='json').status_code,401)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(f'/api/games/sessions/{session.pk}/').status_code,404)
        self.assertEqual(self.client.post(f'/api/games/sessions/{session.pk}/actions/',{'request_id':str(uuid.uuid4()),'revision':0,'action':{'guess':'0123'}},format='json').status_code,404)

    def test_api_never_sends_hidden_answers_and_ignores_claimed_points(self):
        session = self.start()
        response = self.client.get(f'/api/games/sessions/{session.pk}/')
        self.assertNotIn('secret',response.data['state'])
        self.assertNotIn('solution',response.data['state'])
        response = self.client.post(f'/api/games/sessions/{session.pk}/actions/',{'request_id':str(uuid.uuid4()),'revision':0,'action':{'guess':session.puzzle.payload['secret'],'points':999999},'points':999999},format='json')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.data['points_awarded'],5)

    def test_disabled_games_reject_play_and_maintenance_blocks_catalog(self):
        GameSettings.objects.filter(pk=1).update(enabled=False)
        response = self.client.post('/api/games/sessions/',{'game':'code','mode':'daily','request_id':str(uuid.uuid4())},format='json')
        self.assertEqual(response.status_code,503)
        self.assertFalse(self.client.get('/api/games/').data['enabled'])
        SiteSettings.objects.update_or_create(pk=1,defaults={'maintenance_mode':True})
        self.assertEqual(self.client.get('/api/games/').status_code,503)

    def test_game_rate_limit_is_separate_from_other_user_requests(self):
        cache.clear()
        cache.set(f'throttle_user_{self.user.pk}', [time.time()]*120, timeout=60)
        try:
            response = self.client.post('/api/games/sessions/', {'game':'code','mode':'daily','request_id':str(uuid.uuid4())}, format='json')
            self.assertEqual(response.status_code,200)
        finally:
            cache.clear()

    def test_preparation_is_repeatable_readonly_check_and_rejects_overwrite(self):
        original = win(self.user,self.start())
        call_command('prepare_games',stdout=StringIO())
        call_command('prepare_games',check=True,stdout=StringIO())
        self.assertEqual(GamePuzzle.objects.count(),4000)
        self.assertEqual(get_session(self.user,original.pk).status,'won')
        original.puzzle.payload = {'secret':'0000'}
        original.puzzle.save()
        with self.assertRaises(CommandError):
            call_command('prepare_games',stdout=StringIO())

    def test_start_date_cannot_be_changed_after_play(self):
        self.start()
        config = GameSettings.objects.get(pk=1)
        config.start_date -= timedelta(days=1)
        with self.assertRaises(ModelValidationError):
            config.full_clean()

    def test_failed_validation_cannot_activate_incomplete_pack(self):
        GamePuzzle.objects.filter(game='word',pool='daily',slot=0).delete()
        self.assertTrue(validate_database())
        with self.assertRaises(ModelValidationError):
            GameSettings.objects.get(pk=1).full_clean()

    def test_daily_cycle_does_not_repeat_rewards(self):
        session = win(self.user,self.start())
        future = timezone.now()+timedelta(days=400)
        with patch('django.utils.timezone.now',return_value=future):
            next_cycle = self.start()
            self.assertEqual(next_cycle.puzzle_id,session.puzzle_id)
            self.assertFalse(next_cycle.reward_eligible)

    def test_practice_never_selects_future_daily_tasks(self):
        for game in GAMES:
            self.assertEqual(self.start(game,'practice').puzzle.pool,'practice')

    def test_personal_export_includes_own_moves_but_no_hidden_solutions(self):
        from blog.personal_data_export import build_personal_data_export
        own = self.start()
        start_session(self.other,'code','daily',uuid.uuid4())
        exported = build_personal_data_export(self.user)['activity']['game_sessions']
        self.assertEqual([row['id'] for row in exported],[str(own.pk)])
        self.assertEqual(exported[0]['state'],{'guesses':[]})
        self.assertNotIn('payload',exported[0])

    def test_deleting_account_removes_sessions_and_receipts(self):
        self.start()
        self.user.delete()
        self.assertEqual(GameSession.objects.count(),0)
        self.assertEqual(GameRequest.objects.count(),0)
        self.assertEqual(GamePuzzle.objects.count(),4000)

    def test_finite_bank_exhaustion_is_explicit_unrewarded_practice(self):
        puzzles = list(GamePuzzle.objects.filter(game='word',pool='practice'))
        GameSession.objects.bulk_create([GameSession(user=self.user,puzzle=p,mode='practice',day=timezone.localdate(),status='lost',state=initial_state('word'),expires_at=timezone.now()) for p in puzzles])
        session = self.start('word','practice')
        self.assertFalse(session.reward_eligible)


class ConcurrentGameTests(TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_two_wins_with_five_points_remaining_only_award_five(self):
        call_command('prepare_games',start_date=timezone.localdate(),activate=True,stdout=StringIO())
        user = User.objects.create_user(username='parallel')
        GameSettings.objects.filter(pk=1).update(daily_points_cap=5)
        first = start_session(user,'code','daily',uuid.uuid4())
        second = start_session(user,'code','practice',uuid.uuid4())
        def worker(session_id):
            close_old_connections()
            try:
                account = User.objects.get(pk=user.pk)
                session = GameSession.objects.select_related('puzzle').get(pk=session_id)
                return win(account,session).points_awarded
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            awards = list(pool.map(worker,[first.pk,second.pk]))
        self.assertEqual(sum(awards),5)
