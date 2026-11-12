import json
from io import StringIO
from unittest.mock import Mock, patch

import requests
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from .ai import config
from .ai.assistant import build_context, extract_keywords
from .ai.client import AIQuotaExceeded, AIUnavailable, extract_text, run_chat
from .ai.models_catalog import GEMMA_4, MISTRAL_SMALL
from .ai.moderation import ModerationParseError, check_pending_content, parse_moderation_answer
from .ai.summaries import summarize_pending_posts
from .models import (
    AIContentCheck,
    BellSongSuggestion,
    Comments,
    Event,
    Posts,
    ScheduleEntry,
    SiteSettings,
)

AI_KEYS = {'CF_ACCOUNT_ID': 'test-account', 'CF_API_TOKEN': 'test-token'}


def cf_response(text='Готово.', *, status_code=200, success=True, errors=None, openai_format=False):
    if openai_format:
        result = {
            'choices': [{'message': {'role': 'assistant', 'content': text}}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 5},
        }
    else:
        result = {'response': text}
    response = Mock()
    response.status_code = status_code
    response.json.return_value = {'success': success, 'errors': errors or [], 'result': result}
    return response


def enable_ai(**overrides):
    site_settings, _ = SiteSettings.objects.get_or_create(pk=1)
    site_settings.enable_ai = True
    for field, value in overrides.items():
        setattr(site_settings, field, value)
    site_settings.save()
    return site_settings


def make_post(**overrides):
    values = {
        'title': 'Посещение в музея',
        'hook': 'Екскурзия на 11. клас',
        'content': 'Учениците разгледаха **Правец 82** и калкулатора ЕЛКА.',
        'published': True,
        'allowed': True,
    }
    values.update(overrides)
    return Posts.objects.create(**values)


class AIConfigTests(TestCase):
    def test_everything_is_off_without_credentials(self):
        enable_ai()
        self.assertFalse(config.ai_feature_enabled(config.SUMMARIES))

    @override_settings(**AI_KEYS)
    def test_main_switch_is_off_by_default(self):
        SiteSettings.objects.get_or_create(pk=1)
        self.assertFalse(config.ai_feature_enabled(config.SUMMARIES))
        self.assertFalse(config.ai_feature_enabled(config.ASSISTANT))

    @override_settings(**AI_KEYS)
    def test_main_switch_and_feature_switch_are_both_needed(self):
        enable_ai(enable_ai_moderation=False)
        self.assertTrue(config.ai_feature_enabled(config.SUMMARIES))
        self.assertTrue(config.ai_feature_enabled(config.ASSISTANT))
        self.assertFalse(config.ai_feature_enabled(config.MODERATION))

    def test_models_come_from_admin_settings(self):
        site_settings = enable_ai(ai_model_public=MISTRAL_SMALL, ai_model_student_text=GEMMA_4)
        self.assertEqual(config.public_model(site_settings), MISTRAL_SMALL)
        self.assertEqual(config.student_text_model(site_settings), GEMMA_4)

    @override_settings(**AI_KEYS)
    def test_site_status_exposes_effective_switches(self):
        enable_ai(enable_ai_assistant=False)
        data = self.client.get('/api/site-status/').json()
        self.assertTrue(data['ai_summaries_enabled'])
        self.assertFalse(data['ai_assistant_enabled'])
        self.assertNotIn('ai_model_public', data)


@override_settings(**AI_KEYS)
class AIClientTests(TestCase):
    def test_reads_both_answer_formats(self):
        self.assertEqual(extract_text({'response': ' Здравей '}), 'Здравей')
        self.assertEqual(extract_text({'choices': [{'message': {'content': 'Отговор'}}]}), 'Отговор')
        self.assertEqual(extract_text({'choices': [{'message': {'content': ''}}]}), '')

    @patch('blog.ai.client.requests.post')
    def test_gemma_gets_thinking_disabled(self, post):
        post.return_value = cf_response(openai_format=True)
        run_chat(model=GEMMA_4, messages=[], max_tokens=50)
        body = post.call_args.kwargs['json']
        self.assertEqual(body['max_completion_tokens'], 50)
        self.assertEqual(body['chat_template_kwargs'], {'enable_thinking': False})
        self.assertIn('/ai/run/@cf/google/gemma-4-26b-a4b-it', post.call_args.args[0])
        self.assertEqual(post.call_args.kwargs['headers']['Authorization'], 'Bearer test-token')

    @patch('blog.ai.client.requests.post')
    def test_other_models_use_max_tokens(self, post):
        post.return_value = cf_response()
        run_chat(model=MISTRAL_SMALL, messages=[], max_tokens=50)
        body = post.call_args.kwargs['json']
        self.assertEqual(body['max_tokens'], 50)
        self.assertNotIn('chat_template_kwargs', body)

    @patch('blog.ai.client.requests.post')
    def test_quota_and_errors_are_typed(self, post):
        post.return_value = cf_response(status_code=429, success=False)
        with self.assertRaises(AIQuotaExceeded):
            run_chat(model=MISTRAL_SMALL, messages=[])
        post.return_value = cf_response(status_code=400, success=False, errors=[{'code': 4006, 'message': 'limit'}])
        with self.assertRaises(AIQuotaExceeded):
            run_chat(model=MISTRAL_SMALL, messages=[])
        post.return_value = cf_response(status_code=500, success=False)
        with self.assertRaises(AIUnavailable):
            run_chat(model=MISTRAL_SMALL, messages=[])
        post.return_value = cf_response('')
        with self.assertRaises(AIUnavailable):
            run_chat(model=MISTRAL_SMALL, messages=[])
        post.side_effect = requests.ConnectionError()
        with self.assertRaises(AIUnavailable):
            run_chat(model=MISTRAL_SMALL, messages=[])

    @override_settings(CF_API_TOKEN='')
    @patch('blog.ai.client.requests.post')
    def test_no_request_without_credentials(self, post):
        with self.assertRaises(AIUnavailable):
            run_chat(model=MISTRAL_SMALL, messages=[])
        post.assert_not_called()


@override_settings(**AI_KEYS)
class AISummaryTests(TestCase):
    def setUp(self):
        enable_ai()

    @patch('blog.ai.client.requests.post')
    def test_command_summarizes_only_public_posts(self, post):
        post.return_value = cf_response('„Учениците посетиха музея.“', openai_format=True)
        public = make_post()
        draft = make_post(title='Чернова', published=False)
        call_command('run_ai_tasks', '--only', 'summaries', stdout=StringIO())
        public.refresh_from_db()
        draft.refresh_from_db()
        self.assertEqual(public.ai_summary, 'Учениците посетиха музея.')
        self.assertEqual(public.ai_summary_source_hash, public.ai_summary_source_digest())
        self.assertIsNotNone(public.ai_summary_updated_at)
        self.assertEqual(draft.ai_summary, '')
        self.assertEqual(post.call_count, 1)
        self.assertIn('Правец 82', post.call_args.kwargs['json']['messages'][1]['content'])
        self.assertNotIn('**', post.call_args.kwargs['json']['messages'][1]['content'])

    @patch('blog.ai.client.requests.post')
    def test_changed_post_gets_a_new_summary_and_manual_one_is_kept(self, post):
        post.return_value = cf_response('Първо резюме.')
        changed = make_post()
        manual = make_post(title='Ръчно')
        summarize_pending_posts(10)
        Posts.objects.filter(pk=manual.pk).update(ai_summary='Написано от човек.', ai_summary_manual=True)
        changed.content = 'Нов текст на статията.'
        changed.save()

        post.return_value = cf_response('Второ резюме.')
        done, failed = summarize_pending_posts(10)
        changed.refresh_from_db()
        manual.refresh_from_db()
        self.assertEqual((done, failed), (1, 0))
        self.assertEqual(changed.ai_summary, 'Второ резюме.')
        self.assertEqual(manual.ai_summary, 'Написано от човек.')

    @patch('blog.ai.client.requests.post')
    def test_summary_is_hidden_when_feature_is_switched_off(self, post):
        item = make_post()
        Posts.objects.filter(pk=item.pk).update(ai_summary='Резюме.')
        self.assertEqual(self.client.get(f'/api/posts/{item.pk}/').json()['ai_summary'], 'Резюме.')
        enable_ai(enable_ai_summaries=False)
        self.assertEqual(self.client.get(f'/api/posts/{item.pk}/').json()['ai_summary'], '')

    @patch('blog.ai.client.requests.post')
    def test_command_does_nothing_when_switched_off(self, post):
        enable_ai(enable_ai=False)
        make_post()
        out = StringIO()
        call_command('run_ai_tasks', stdout=out)
        post.assert_not_called()
        self.assertIn('изключено', out.getvalue())

    @patch('blog.ai.client.requests.post')
    def test_quota_stops_the_run_without_failing(self, post):
        post.return_value = cf_response(status_code=429, success=False)
        make_post()
        out = StringIO()
        call_command('run_ai_tasks', stdout=out)
        self.assertIn('лимит', out.getvalue())


@override_settings(**AI_KEYS)
class AIModerationTests(TestCase):
    def setUp(self):
        enable_ai()
        self.user = User.objects.create_user('ученик', password='x-Pass-123456')
        self.post = make_post()

    def test_parse_answer(self):
        self.assertEqual(
            parse_moderation_answer('Отговор: {"flagged": true, "category": "insult", "reason": "Обида"}'),
            (True, 'insult', 'Обида'),
        )
        self.assertEqual(parse_moderation_answer('{"flagged": false, "category": "none"}'), (False, '', ''))
        self.assertEqual(parse_moderation_answer('{"flagged": true, "category": "xyz"}')[1], 'other')
        with self.assertRaises(ModerationParseError):
            parse_moderation_answer('няма json')

    @patch('blog.ai.client.requests.post')
    def test_flags_comment_and_song(self, post):
        comment = Comments.objects.create(content='Ти си идиот', user=self.user, post=self.post)
        song = BellSongSuggestion.objects.create(title='Песен', link='https://youtu.be/x', user=self.user)
        post.side_effect = [
            cf_response('{"flagged": true, "category": "insult", "reason": "Обидна дума"}'),
            cf_response('{"flagged": false, "category": "none", "reason": ""}'),
        ]
        self.assertEqual(check_pending_content(10), (2, 1))
        comment_check = comment.ai_checks.get()
        self.assertEqual(comment_check.verdict, AIContentCheck.VERDICT_FLAGGED)
        self.assertEqual(comment_check.category, 'insult')
        self.assertEqual(comment_check.model_name, MISTRAL_SMALL)
        self.assertEqual(song.ai_checks.get().verdict, AIContentCheck.VERDICT_OK)
        prompt = post.call_args_list[0].kwargs['json']['messages'][1]['content']
        self.assertIn('<<<ТЕКСТ>>>', prompt)
        # Already checked content is not sent again.
        self.assertEqual(check_pending_content(10), (0, 0))

    @patch('blog.ai.client.requests.post')
    def test_invalid_answer_is_retried_then_given_up(self, post):
        comment = Comments.objects.create(content='Здравейте', user=self.user, post=self.post)
        post.return_value = cf_response('не е json')
        for _ in range(3):
            check_pending_content(10)
        check = comment.ai_checks.get()
        self.assertEqual(check.verdict, AIContentCheck.VERDICT_ERROR)
        self.assertEqual(check.attempts, 3)
        self.assertEqual(check_pending_content(10), (0, 0))

    @patch('blog.ai.client.requests.post')
    def test_check_is_deleted_with_the_comment(self, post):
        comment = Comments.objects.create(content='Текст', user=self.user, post=self.post)
        post.return_value = cf_response('{"flagged": false}')
        check_pending_content(10)
        self.assertEqual(AIContentCheck.objects.count(), 1)
        comment.delete()
        self.assertEqual(AIContentCheck.objects.count(), 0)

    @patch('blog.ai.client.requests.post')
    def test_admin_lists_show_the_ai_column_and_filter(self, post):
        admin_user = User.objects.create_superuser('admin', 'admin@example.test', 'x-Pass-123456')
        comment = Comments.objects.create(content='Лош коментар', user=self.user, post=self.post)
        AIContentCheck.objects.create(
            content_type=ContentType.objects.get_for_model(Comments),
            object_id=comment.pk,
            verdict=AIContentCheck.VERDICT_FLAGGED,
            category='insult',
            reason='Обида',
        )
        self.client.force_login(admin_user)
        response = self.client.get(reverse('admin:blog_comments_changelist'), {'ai_check': 'flagged'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Лош коментар')
        self.assertContains(response, 'Обида')
        settings_page = self.client.get(reverse('admin:blog_sitesettings_change', args=(1,)))
        self.assertContains(settings_page, 'ИИ функции (общо копче)')
        self.assertContains(settings_page, 'Gemma 4 26B')


@override_settings(**AI_KEYS, AI_ASSISTANT_DAILY_LIMIT=50)
class AIAssistantApiTests(APITestCase):
    url = '/api/ai/assistant/'

    def setUp(self):
        cache.clear()
        enable_ai()
        self.post = make_post(title='Коледна ваканция', hook='Важни дати', content='Ваканцията започва на 22 декември.')

    @patch('blog.ai.client.requests.post')
    def test_disabled_assistant_returns_503(self, post):
        enable_ai(enable_ai_assistant=False)
        response = self.client.post(self.url, {'question': 'Кога е ваканцията?'}, format='json')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['code'], 'ai_disabled')
        post.assert_not_called()

    @patch('blog.ai.client.requests.post')
    def test_anonymous_visitor_needs_turnstile_then_gets_a_session(self, post):
        post.return_value = cf_response('Ваканцията започва на 22 декември.')
        response = self.client.post(self.url, {'question': 'Кога е ваканцията?'}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['code'], 'turnstile_required')

        with patch('blog.ai.views.verify_turnstile', return_value=False):
            response = self.client.post(self.url, {'question': 'Кога?', 'turnstile_token': 'bad'}, format='json')
        self.assertEqual(response.json()['code'], 'turnstile_failed')

        with patch('blog.ai.views.verify_turnstile', return_value=True):
            response = self.client.post(
                self.url, {'question': 'Кога е ваканцията?', 'turnstile_token': 'ok'}, format='json'
            )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['answer'], 'Ваканцията започва на 22 декември.')
        self.assertIn({'title': 'Коледна ваканция', 'url': f'/post/{self.post.pk}'}, data['sources'])
        self.assertTrue(data['ai_session'])

        response = self.client.post(
            self.url, {'question': 'А кога свършва?', 'ai_session': data['ai_session']}, format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('ai_session', response.json())

        forged = self.client.post(self.url, {'question': 'Кога?', 'ai_session': 'fake:value'}, format='json')
        self.assertEqual(forged.json()['code'], 'turnstile_required')

    @patch('blog.ai.client.requests.post')
    def test_logged_in_user_skips_turnstile_and_history_is_sent(self, post):
        post.return_value = cf_response('Отговор.')
        self.client.force_authenticate(User.objects.create_user('ученик', password='x-Pass-123456'))
        response = self.client.post(self.url, {
            'question': 'Кога е ваканцията?',
            'history': [
                {'role': 'user', 'content': 'Здравей'},
                {'role': 'assistant', 'content': 'Здравейте!'},
            ],
        }, format='json')
        self.assertEqual(response.status_code, 200)
        messages = post.call_args.kwargs['json']['messages']
        self.assertEqual([m['role'] for m in messages], ['system', 'user', 'assistant', 'user'])
        self.assertIn('Ваканцията започва на 22 декември', messages[-1]['content'])
        self.assertEqual(post.call_args.kwargs['json']['max_tokens'], 350)

    @patch('blog.ai.client.requests.post')
    def test_invalid_input_is_rejected(self, post):
        self.client.force_authenticate(User.objects.create_user('ученик', password='x-Pass-123456'))
        too_long = self.client.post(self.url, {'question': 'а' * 501}, format='json')
        bad_role = self.client.post(
            self.url, {'question': 'Здравей', 'history': [{'role': 'system', 'content': 'x'}]}, format='json'
        )
        self.assertEqual(too_long.status_code, 400)
        self.assertEqual(bad_role.status_code, 400)
        post.assert_not_called()

    @override_settings(AI_ASSISTANT_DAILY_LIMIT=1)
    @patch('blog.ai.client.requests.post')
    def test_site_wide_daily_limit_and_failed_answers_do_not_count(self, post):
        self.client.force_authenticate(User.objects.create_user('ученик', password='x-Pass-123456'))
        post.return_value = cf_response(status_code=500, success=False)
        failed = self.client.post(self.url, {'question': 'Кога е ваканцията?'}, format='json')
        self.assertEqual(failed.json()['code'], 'ai_unavailable')

        post.return_value = cf_response('Отговор.')
        self.assertEqual(self.client.post(self.url, {'question': 'Въпрос 1'}, format='json').status_code, 200)
        limited = self.client.post(self.url, {'question': 'Въпрос 2'}, format='json')
        self.assertEqual(limited.status_code, 503)
        self.assertEqual(limited.json()['code'], 'ai_daily_limit')


class AIAssistantContextTests(TestCase):
    def test_keywords_drop_common_words_and_stem(self):
        self.assertEqual(extract_keywords('Кога е коледната ваканция?'), ['коледн', 'ваканц'])

    def test_context_uses_faq_events_and_class_schedule(self):
        site_settings, _ = SiteSettings.objects.get_or_create(pk=1)
        ScheduleEntry.objects.create(class_name='11Б', lesson_number=1, monday='Математика')
        from django.utils import timezone
        from datetime import timedelta

        Event.objects.create(
            title='Ден на отворените врати',
            start_datetime=timezone.now() + timedelta(days=3),
            location='Актова зала',
            description='Покана за всички',
            attendees_text='Всички',
        )
        context, sources = build_context('Как да предложа песен за звънеца на 11б?', site_settings)
        self.assertIn('Как мога да предложа песен за училищния звънец?', context)
        self.assertIn('Ден на отворените врати', context)
        self.assertIn('Седмична програма на 11Б клас', context)
        self.assertIn('Математика', context)
        urls = [source['url'] for source in sources]
        self.assertIn('/faq', urls)
        self.assertIn('/program', urls)
        # The upcoming event is in the context but did not match the question.
        self.assertNotIn('/events', urls)
        _, sources = build_context('Кога е денят на отворените врати?', site_settings)
        self.assertIn('/events', [source['url'] for source in sources])

    def test_schedule_is_skipped_when_program_page_is_off(self):
        site_settings, _ = SiteSettings.objects.get_or_create(pk=1)
        site_settings.enable_program_page = False
        ScheduleEntry.objects.create(class_name='11Б', lesson_number=1, monday='Математика')
        context, _ = build_context('Програмата на 11б', site_settings)
        self.assertNotIn('Математика', context)
