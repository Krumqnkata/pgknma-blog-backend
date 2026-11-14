import json
import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from .ai import retrieval
from .ai.assistant import answer_question
from .ai.client import EMBEDDING_MODEL
from .ai.intents import AskContext, match_builtin
from .ai.knowledge_index import pack, sync_knowledge
from .ai.rules import log_unanswered, match_admin_rule
from .ai.text import find_class, find_day, find_lesson, normalize, stems
from .data_retention import cleanup_personal_data
from .models import (
    AssistantConversation,
    AssistantMessage,
    AssistantRule,
    AssistantUnansweredQuestion,
    Event,
    KnowledgeChunk,
    MemeOfWeek,
    PollOption,
    PollQuestion,
    Posts,
    ScheduleEntry,
    SiteSettings,
    UserProfile,
    meme_week_start,
)
from .personal_data_export import build_personal_data_export

AI_KEYS = {'CF_ACCOUNT_ID': 'test-account', 'CF_API_TOKEN': 'test-token'}


def enable_assistant(**overrides):
    site_settings, _ = SiteSettings.objects.get_or_create(pk=1)
    site_settings.enable_ai = True
    site_settings.enable_ai_assistant = True
    for name, value in overrides.items():
        setattr(site_settings, name, value)
    site_settings.save()
    return site_settings


def fake_cloudflare(answer='Отговор от ИИ.', vectors=None):
    """requests.post replacement: embeddings get vectors, chat gets `answer`."""
    calls = {'chat': [], 'embed': []}

    def post(url, json=None, **kwargs):
        response = Mock()
        response.status_code = 200
        if EMBEDDING_MODEL in url:
            calls['embed'].append(json)
            texts = json['text']
            data = [list(vectors(text)) if vectors else [1.0, 0.0, 0.0] for text in texts]
            response.json.return_value = {'success': True, 'result': {'data': data, 'shape': [len(data), 3]}}
        else:
            calls['chat'].append(json)
            response.json.return_value = {'success': True, 'result': {'response': answer}}
        return response

    return post, calls


class TextTests(TestCase):
    def test_shlyokavitsa_and_stems(self):
        self.assertEqual(normalize('Kakvo imame v sriada 11b?'), 'какво имаме в сряда 11б')
        self.assertEqual(normalize('kolko to4ki imam'), 'колко точки имам')
        self.assertEqual(stems('мемета меме мемето'), ['мем'])
        self.assertEqual(stems('Кога е коледната ваканция?'), ['коледн', 'ваканц'])

    def test_class_day_and_lesson(self):
        saturday = date(2026, 11, 14)
        self.assertEqual(find_class('програма на 11 б'), '11Б')
        self.assertEqual(find_class('9v'), '9В')
        self.assertIsNone(find_class('имаме 15 минути'))
        self.assertEqual(find_lesson('кой е 3-ти час'), 3)
        self.assertEqual(find_lesson('първи час'), 1)
        self.assertEqual(find_day('утре', saturday), date(2026, 11, 15))
        self.assertEqual(find_day('в сряда', saturday), date(2026, 11, 18))
        self.assertIsNone(find_day('кога', saturday))


class BuiltinRuleTests(TestCase):
    def setUp(self):
        self.site_settings = enable_assistant()
        self.wednesday = date(2026, 11, 18)
        for number, subject in ((1, 'Математика'), (2, 'БЕЛ'), (3, 'Физика')):
            ScheduleEntry.objects.create(class_name='11Б', lesson_number=number, wednesday=subject, monday='История')

    def ask(self, question, *, user=None, history=(), today=None):
        return match_builtin(AskContext(question, user=user, history=list(history), site_settings=self.site_settings, today=today or self.wednesday))

    def test_schedule_for_a_day_and_a_lesson(self):
        answer = self.ask('Какво имаме днес 11б?')
        self.assertEqual(answer.key, 'schedule')
        self.assertIn('Програма на 11Б за сряда (18.11.2026)', answer.text)
        self.assertIn('2. час — БЕЛ', answer.text)
        lesson = self.ask('кой е 3-ти час в сряда на 11 б')
        self.assertIn('3. час: Физика', lesson.text)

    def test_schedule_uses_the_class_from_the_conversation(self):
        history = [{'role': 'user', 'content': 'програма 11б'}, {'role': 'assistant', 'content': '...'}]
        answer = self.ask('а в понеделник?', history=history)
        self.assertEqual(answer.key, 'schedule')
        self.assertIn('1. час — История', answer.text)

    def test_schedule_asks_for_the_class_and_respects_the_switch(self):
        self.assertIn('За кой клас?', self.ask('покажи програмата').text)
        self.site_settings.enable_program_page = False
        self.assertIn('изключена', self.ask('програма 11б').text)

    def test_weekend_moves_to_monday(self):
        answer = self.ask('програма 11б в събота')
        self.assertIn('В събота и неделя няма часове', answer.text)
        self.assertIn('понеделник', answer.text)

    def test_events_on_a_day_and_upcoming(self):
        tomorrow = timezone.localdate() + timedelta(days=1)
        start = timezone.make_aware(timezone.datetime.combine(tomorrow, timezone.datetime.min.time()) + timedelta(hours=10))
        Event.objects.create(title='Ден на отворените врати', start_datetime=start, location='Актова зала', description='Покана', attendees_text='Всички')
        on_day = self.ask('какво има утре?', today=timezone.localdate())
        self.assertEqual(on_day.key, 'events_on_day')
        self.assertIn('Ден на отворените врати', on_day.text)
        upcoming = self.ask('какви събития предстоят')
        self.assertEqual(upcoming.key, 'events_upcoming')
        self.assertIn('Актова зала', upcoming.text)

    def test_holidays_without_entries_points_to_school_site(self):
        answer = self.ask('Кога е ваканцията?')
        self.assertEqual(answer.key, 'holidays')
        self.assertIn('pgknma.com', answer.text)

    def test_poll_does_not_reveal_the_correct_answer(self):
        now = timezone.now()
        poll = PollQuestion.objects.create(title='Коя е столицата?', start_date=now - timedelta(days=1), end_date=now + timedelta(days=2))
        PollOption.objects.create(question=poll, text='София', key='a', is_correct=True)
        PollOption.objects.create(question=poll, text='Пловдив', key='b')
        answer = self.ask('каква е анкетата')
        self.assertIn('Коя е столицата?', answer.text)
        self.assertIn('София; Пловдив', answer.text)
        self.assertNotIn('правил', answer.text.lower())

    def test_memes_leader_of_the_week(self):
        author = User.objects.create_user('автор', password='x-Pass-123456')
        meme = MemeOfWeek.objects.create(user=author, title='Смешно', status='approved', week_start=meme_week_start(self.wednesday))
        MemeOfWeek.objects.filter(pk=meme.pk).update(votes=4)
        answer = self.ask('кое меме води?')
        self.assertIn('„Смешно“ от автор — 4 гласа', answer.text)

    def test_personal_stats_need_login(self):
        self.assertEqual(self.ask('колко точки имам').key, 'personal_login')
        user = User.objects.create_user('ученик', password='x-Pass-123456')
        UserProfile.objects.update_or_create(user=user, defaults={'gamification_points': 37})
        answer = self.ask('колко точки имам', user=user)
        self.assertEqual(answer.key, 'personal_stats')
        self.assertIn('Имате 37 точки', answer.text)

    def test_about_site_never_calls_the_blog_the_school_website(self):
        answer = self.ask('Това ли е официалният сайт на училището?')
        self.assertEqual(answer.key, 'about_site')
        self.assertIn('официалният блог', answer.text)
        self.assertIn('Уебсайтът на гимназията е pgknma.com', answer.text)
        admission = self.ask('как се кандидатства за прием')
        self.assertEqual(admission.sources[0]['url'], 'https://pgknma.com/priem')

    def test_greeting_and_unknown(self):
        self.assertEqual(self.ask('здрасти').key, 'greeting')
        self.assertIsNone(self.ask('Кой е любимият цвят на директора?'))


class AdminRuleTests(TestCase):
    def test_most_specific_phrase_wins_and_hits_are_counted(self):
        general = AssistantRule.objects.create(title='Ваканции', phrases='ваканция', answer='Общ отговор.')
        specific = AssistantRule.objects.create(
            title='Коледа', phrases='коледна ваканция\nkoledna vakancia', answer='От 22 декември.',
            link_title='Календар', link_url='/events',
        )
        answer = match_admin_rule('Кога е коледната ваканция?')
        self.assertEqual(answer.text, 'От 22 декември.')
        self.assertEqual(answer.sources, [{'title': 'Календар', 'url': '/events'}])
        self.assertEqual(match_admin_rule('koledna vakanciq').key, f'admin:{specific.pk}')
        self.assertEqual(match_admin_rule('има ли ваканции').key, f'admin:{general.pk}')
        specific.refresh_from_db()
        self.assertEqual(specific.hit_count, 2)
        self.assertIsNone(match_admin_rule('какво е времето'))

    def test_unanswered_questions_are_counted_without_user(self):
        log_unanswered('Кой е директорът?')
        log_unanswered('кой е ДИРЕКТОРЪТ')
        row = AssistantUnansweredQuestion.objects.get()
        self.assertEqual(row.times_asked, 2)
        self.assertFalse(hasattr(row, 'user'))


@override_settings(**AI_KEYS)
class AnswerFlowTests(TestCase):
    def setUp(self):
        self.site_settings = enable_assistant()
        KnowledgeChunk.objects.create(
            source_type='page', source_id='team', title='Екипът на сайта', url='/developers',
            text='Сайтът се прави от Александър и Крум с ментор инж. Людмил Бонев.',
            stems=' '.join(stems('Екипът на сайта Сайтът се прави от Александър и Крум с ментор инж. Людмил Бонев')),
            content_hash='x',
        )

    def test_rules_answer_without_calling_cloudflare(self):
        with patch('blog.ai.client.requests.post') as post:
            result = answer_question('здравей', site_settings=self.site_settings)
        self.assertEqual(result.answer_source, 'rule')
        post.assert_not_called()

    def test_ai_answers_from_the_matching_knowledge(self):
        fake, calls = fake_cloudflare('Сайтът се прави от Александър и Крум.')
        with patch('blog.ai.client.requests.post', side_effect=fake):
            result = answer_question('Кой прави сайта, екипът?', site_settings=self.site_settings)
        self.assertEqual(result.answer_source, 'ai')
        self.assertEqual(result.sources, [{'title': 'Екипът на сайта', 'url': '/developers'}])
        prompt = calls['chat'][0]['messages'][-1]['content']
        self.assertIn('Людмил Бонев', prompt)
        self.assertIn('pgknma.com', calls['chat'][0]['messages'][0]['content'])

    def test_unknown_marker_logs_the_question(self):
        fake, _ = fake_cloudflare('Не знам това. [[НЕ_ЗНАМ]]')
        with patch('blog.ai.client.requests.post', side_effect=fake):
            result = answer_question('Кой е екипът на кухнята?', site_settings=self.site_settings)
        self.assertEqual(result.answer_source, 'none')
        self.assertNotIn('[[', result.answer)
        self.assertTrue(AssistantUnansweredQuestion.objects.filter(question='Кой е екипът на кухнята?').exists())

    def test_rules_only_mode_never_calls_cloudflare(self):
        self.site_settings.ai_assistant_use_ai = False
        with patch('blog.ai.client.requests.post') as post:
            result = answer_question('Кой прави сайта, екипът?', site_settings=self.site_settings)
        post.assert_not_called()
        self.assertEqual(result.answer_source, 'none')
        self.assertEqual(AssistantUnansweredQuestion.objects.count(), 1)

    def test_daily_limit_only_guards_ai_answers(self):
        with patch('blog.ai.client.requests.post') as post:
            limited = answer_question('Кой прави сайта, екипът?', site_settings=self.site_settings, reserve_ai=lambda: False)
            rule = answer_question('мерси', site_settings=self.site_settings, reserve_ai=lambda: False)
        post.assert_not_called()
        self.assertEqual(limited.rule_key, 'daily_limit')
        self.assertEqual(rule.answer_source, 'rule')

    def test_personal_data_goes_to_the_ai_only_for_personal_questions(self):
        user = User.objects.create_user('ученик', password='x-Pass-123456')
        UserProfile.objects.update_or_create(user=user, defaults={'gamification_points': 12})
        fake, calls = fake_cloudflare('Отговор.')
        with patch('blog.ai.client.requests.post', side_effect=fake):
            answer_question('Какво още ми остава да направя на сайта, екипът?', user=user, site_settings=self.site_settings)
            answer_question('Кой прави сайта, екипът?', user=user, site_settings=self.site_settings)
        self.assertIn('ЛИЧНИ ДАННИ', calls['chat'][0]['messages'][-1]['content'])
        self.assertIn('Точки: 12', calls['chat'][0]['messages'][-1]['content'])
        self.assertNotIn('ЛИЧНИ ДАННИ', calls['chat'][1]['messages'][-1]['content'])


@override_settings(**AI_KEYS)
class KnowledgeIndexTests(TestCase):
    def setUp(self):
        enable_assistant()
        retrieval._cache['key'] = None
        self.site_file = tempfile.NamedTemporaryFile('w', suffix='.json', delete=False, encoding='utf-8')
        json.dump({'sections': [
            {'id': 'contact', 'title': 'Контакт', 'url': '/contact', 'text': 'Телефон: +359 88 499 4500. Адрес: гр. Стара Загора.'},
            {'id': 'bad', 'title': 'Лош', 'url': 'javascript:alert(1)', 'text': 'Нищо'},
        ]}, self.site_file)
        self.site_file.close()

    def tearDown(self):
        Path(self.site_file.name).unlink(missing_ok=True)

    def test_sync_builds_updates_and_cleans_the_index(self):
        post = Posts.objects.create(title='Олимпиада', hook='Успех', content='Отборът спечели **злато**.', published=True, allowed=True)
        fake, calls = fake_cloudflare()
        with override_settings(AI_SITE_KNOWLEDGE_PATH=self.site_file.name), patch('blog.ai.client.requests.post', side_effect=fake):
            first = sync_knowledge()
            self.assertGreater(first['created'], 0)
            self.assertEqual(first['embedded'], KnowledgeChunk.objects.count())
            self.assertTrue(KnowledgeChunk.objects.filter(source_type='page', source_id='contact').exists())
            self.assertFalse(KnowledgeChunk.objects.filter(source_id='bad').exists())
            self.assertTrue(KnowledgeChunk.objects.filter(source_type='core').exists())
            self.assertIn('злато', KnowledgeChunk.objects.get(source_type='post', source_id=str(post.pk)).text)

            embedded_before = len(calls['embed'])
            second = sync_knowledge()
            self.assertEqual((second['created'], second['updated'], second['embedded']), (0, 0, 0))
            self.assertEqual(len(calls['embed']), embedded_before)

            post.content = 'Отборът спечели сребро.'
            post.save()
            third = sync_knowledge()
            self.assertEqual(third['updated'], 1)
            self.assertEqual(third['embedded'], 1)

            post.delete()
            self.assertEqual(sync_knowledge()['deleted'], 1)

    def test_frontend_pages_are_kept_when_the_file_is_missing(self):
        with override_settings(AI_SITE_KNOWLEDGE_PATH=self.site_file.name), patch('blog.ai.client.requests.post', side_effect=fake_cloudflare()[0]):
            sync_knowledge()
        with override_settings(AI_SITE_KNOWLEDGE_PATH=str(Path(self.site_file.name).with_suffix('.missing'))), \
                patch('blog.ai.client.requests.post', side_effect=fake_cloudflare()[0]):
            sync_knowledge()
        self.assertTrue(KnowledgeChunk.objects.filter(source_type='page', source_id='contact').exists())

    def test_hybrid_search_prefers_the_semantic_match(self):
        def vectors(text):
            return [1.0, 0.0, 0.0] if 'звънец' in text or 'песен' in text else [0.0, 1.0, 0.0]

        for source_id, title, text, vector in (
            ('songs', 'Песни', 'Предложете песен за звънеца.', [1.0, 0.0, 0.0]),
            ('team', 'Екип', 'Хората, които правят сайта.', [0.0, 1.0, 0.0]),
        ):
            KnowledgeChunk.objects.create(
                source_type='page', source_id=source_id, title=title, url=f'/{source_id}', text=text,
                stems=' '.join(stems(f'{title} {text}')), content_hash=source_id,
                embedding=pack(vector), embedding_model=EMBEDDING_MODEL,
            )
        with patch('blog.ai.client.requests.post', side_effect=fake_cloudflare(vectors=vectors)[0]):
            hits = retrieval.search('Как да пусна музика на звънеца?')
        self.assertEqual(hits[0].title, 'Песни')

        retrieval._cache['key'] = None
        with patch('blog.ai.client.requests.post', side_effect=AssertionError('no network')):
            keyword_hits = retrieval.search('Хората, които правят сайта', use_embeddings=False)
        self.assertEqual(keyword_hits[0].title, 'Екип')


@override_settings(**AI_KEYS, AI_ASSISTANT_DAILY_LIMIT=50)
class ConversationApiTests(APITestCase):
    url = '/api/ai/assistant/'

    def setUp(self):
        cache.clear()
        enable_assistant()
        self.user = User.objects.create_user('ученик', password='x-Pass-123456')
        self.other = User.objects.create_user('друг', password='x-Pass-123456')

    def test_logged_in_conversation_is_saved_continued_and_private(self):
        self.client.force_authenticate(self.user)
        first = self.client.post(self.url, {'question': 'здравей'}, format='json').json()
        self.assertEqual(first['answer_source'], 'rule')
        conversation_id = first['conversation_id']

        second = self.client.post(self.url, {'question': 'мерси', 'conversation_id': conversation_id}, format='json').json()
        self.assertEqual(second['conversation_id'], conversation_id)

        listing = self.client.get('/api/ai/conversations/').json()
        self.assertEqual([item['id'] for item in listing], [conversation_id])
        detail = self.client.get(f'/api/ai/conversations/{conversation_id}/').json()
        self.assertEqual([m['role'] for m in detail['messages']], ['user', 'assistant', 'user', 'assistant'])
        self.assertEqual(detail['messages'][1]['answer_source'], 'rule')

        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(f'/api/ai/conversations/{conversation_id}/').status_code, 404)
        self.assertEqual(self.client.post(self.url, {'question': 'мерси', 'conversation_id': conversation_id}, format='json').status_code, 404)

        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.delete(f'/api/ai/conversations/{conversation_id}/').status_code, 204)
        self.assertFalse(AssistantConversation.objects.exists())

    def test_anonymous_questions_are_not_stored(self):
        with patch('blog.ai.views.verify_turnstile', return_value=True):
            data = self.client.post(self.url, {'question': 'здравей', 'turnstile_token': 'ok'}, format='json').json()
        self.assertEqual(data['answer_source'], 'rule')
        self.assertNotIn('conversation_id', data)
        self.assertFalse(AssistantMessage.objects.exists())
        self.assertEqual(self.client.get('/api/ai/conversations/').status_code, 401)

    def test_visible_without_keys_and_answers_by_rules(self):
        with override_settings(CF_API_TOKEN=''):
            self.assertTrue(self.client.get('/api/site-status/').json()['ai_assistant_enabled'])
            self.client.force_authenticate(self.user)
            data = self.client.post(self.url, {'question': 'програма 11б'}, format='json').json()
        self.assertEqual(data['answer_source'], 'rule')

    def test_old_conversations_are_cleaned_and_exported(self):
        conversation = AssistantConversation.objects.create(user=self.user, title='Стар')
        AssistantMessage.objects.create(conversation=conversation, role='user', content='Въпрос')
        export = build_personal_data_export(self.user)
        self.assertEqual(export['activity']['assistant_conversations'][0]['messages'][0]['content'], 'Въпрос')

        AssistantConversation.objects.filter(pk=conversation.pk).update(updated_at=timezone.now() - timedelta(days=91))
        log_unanswered('стар въпрос')
        AssistantUnansweredQuestion.objects.update(last_asked_at=timezone.now() - timedelta(days=181))
        counts = cleanup_personal_data(days=90)
        self.assertEqual(counts['assistant_conversations'], 1)
        self.assertEqual(counts['assistant_unanswered_questions'], 1)
        self.assertFalse(AssistantConversation.objects.exists())


class FollowUpFixTests(TestCase):
    """Cases found in the live check."""

    def setUp(self):
        self.site_settings = enable_assistant()

    def test_events_for_this_and_next_week(self):
        today = timezone.localdate()
        next_monday = today - timedelta(days=today.weekday()) + timedelta(days=7)
        start = timezone.make_aware(timezone.datetime.combine(next_monday, timezone.datetime.min.time()) + timedelta(hours=9))
        Event.objects.create(title='Състезание', start_datetime=start, location='Зала', description='-', attendees_text='Всички')
        answer = match_builtin(AskContext('Какво има следващата седмица?', site_settings=self.site_settings))
        self.assertEqual(answer.key, 'events_week')
        self.assertIn('Състезание', answer.text)
        this_week = match_builtin(AskContext('Какви събития има тази седмица?', site_settings=self.site_settings))
        self.assertNotIn('Състезание', this_week.text)

    @override_settings(**AI_KEYS)
    def test_marker_on_a_real_answer_is_ignored(self):
        KnowledgeChunk.objects.create(
            source_type='post', source_id='1', title='Олимпиада', url='/post/1',
            text='Иван и Мария от 11Б спечелиха олимпиадата по информатика.',
            stems=' '.join(stems('Олимпиада Иван и Мария от 11Б спечелиха олимпиадата по информатика')),
            content_hash='x',
        )
        retrieval._cache['key'] = None
        fake, _ = fake_cloudflare('Олимпиадата по информатика спечелиха Иван и Мария от 11Б. [[НЕ_ЗНАМ]]')
        with patch('blog.ai.client.requests.post', side_effect=fake):
            result = answer_question('Кой спечели олимпиадата по информатика?', site_settings=self.site_settings)
        self.assertEqual(result.answer_source, 'ai')
        self.assertNotIn('[[', result.answer)
        self.assertEqual(result.sources, [{'title': 'Олимпиада', 'url': '/post/1'}])
        self.assertFalse(AssistantUnansweredQuestion.objects.exists())
