import json
import re
from datetime import date
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from .ai import polls, prompts
from .ai.client import AIQuotaExceeded, AIResult, AIUnavailable
from .models import PollDraft, PollOption, PollQuestion, SiteSettings

AI_KEYS = {'CF_ACCOUNT_ID': 'test-account', 'CF_API_TOKEN': 'test-token'}
MONDAY = date(2026, 11, 16)
THURSDAY = date(2026, 11, 12)
WEDNESDAY = date(2026, 11, 11)

QUIZ = {
    'title': 'Езици за програмиране',
    'question': 'Кой език е създаден от Гуидо ван Росум?',
    'options': {'a': 'Python', 'b': 'Java', 'c': 'C#', 'd': 'Ruby'},
    'correct': 'a',
    'explanation': 'Python е създаден от Гуидо ван Росум през 1991 г.',
}


def enable_poll_ai(**overrides):
    site_settings, _ = SiteSettings.objects.get_or_create(pk=1)
    site_settings.enable_ai = True
    site_settings.enable_ai_poll_drafts = True
    site_settings.ai_poll_auto_publish = True
    for field, value in overrides.items():
        setattr(site_settings, field, value)
    site_settings.save()
    return site_settings


def fake_model(*, quiz=QUIZ, verify_answer='correct', generation_text=None):
    """Stand-in for run_chat. verify_answer: 'correct', 'wrong' or a letter."""

    def run_chat(*, model, messages, **kwargs):
        system, user = messages[0]['content'], messages[1]['content']
        if system == prompts.POLL_DRAFT_SYSTEM:
            return AIResult(text=generation_text or json.dumps(quiz, ensure_ascii=False), model=model)
        correct_text = quiz['options'][quiz['correct']]
        letters = dict(re.findall(r'^([A-D])\) (.+)$', user, flags=re.MULTILINE))
        right = next(letter for letter, text in letters.items() if text == correct_text).lower()
        if verify_answer == 'correct':
            answer = right
        elif verify_answer == 'wrong':
            answer = next(key for key in 'abcd' if key != right)
        else:
            answer = verify_answer
        return AIResult(text=json.dumps({'answer': answer}), model=model)

    return run_chat


def make_draft(**overrides):
    values = {
        'week_start': MONDAY,
        'title': 'Кой е създал Linux?',
        'task_description': 'Кой е създал ядрото Linux?',
        'option_a': 'Линус Торвалдс',
        'option_b': 'Бил Гейтс',
        'option_c': 'Стив Джобс',
        'option_d': 'Денис Ричи',
        'correct_key': 'a',
        'verification': PollDraft.Verification.CONFIRMED,
    }
    values.update(overrides)
    return PollDraft.objects.create(**values)


class ParsePollDraftTests(TestCase):
    def test_parses_json_inside_code_fence(self):
        parsed = polls.parse_poll_draft('```json\n' + json.dumps(QUIZ, ensure_ascii=False) + '\n```')
        self.assertEqual(parsed['title'], 'Езици за програмиране')
        self.assertEqual(parsed['options'], ['Python', 'Java', 'C#', 'Ruby'])
        self.assertEqual(parsed['correct'], 0)

    def test_rejects_duplicate_options(self):
        quiz = dict(QUIZ, options={'a': 'Python', 'b': 'python', 'c': 'C#', 'd': 'Ruby'})
        with self.assertRaises(polls.PollDraftParseError):
            polls.parse_poll_draft(json.dumps(quiz))

    def test_rejects_missing_correct_answer(self):
        with self.assertRaises(polls.PollDraftParseError):
            polls.parse_poll_draft(json.dumps(dict(QUIZ, correct='e')))

    def test_rejects_text_without_json(self):
        with self.assertRaises(polls.PollDraftParseError):
            polls.parse_poll_draft('Ето един въпрос за вас!')

    def test_verification_accepts_cyrillic_letter(self):
        self.assertEqual(polls.parse_verification('{"answer": "Б"}'), 'b')


@override_settings(**AI_KEYS)
class GenerateDraftsTests(TestCase):
    def setUp(self):
        self.site_settings = enable_poll_ai()

    def test_creates_three_confirmed_drafts_with_correct_answer_kept(self):
        with patch('blog.ai.polls.run_chat', side_effect=fake_model()):
            created, failed = polls.generate_drafts_for_week(MONDAY, self.site_settings)

        self.assertEqual((created, failed), (3, 0))
        drafts = PollDraft.objects.filter(week_start=MONDAY)
        self.assertEqual(drafts.count(), 3)
        for draft in drafts:
            self.assertEqual(draft.verification, PollDraft.Verification.CONFIRMED)
            self.assertEqual(getattr(draft, f'option_{draft.correct_key}'), 'Python')

    def test_marks_draft_disputed_when_second_check_disagrees(self):
        with patch('blog.ai.polls.run_chat', side_effect=fake_model(verify_answer='wrong')):
            polls.generate_drafts_for_week(MONDAY, self.site_settings)

        draft = PollDraft.objects.filter(week_start=MONDAY).first()
        self.assertEqual(draft.verification, PollDraft.Verification.DISPUTED)
        self.assertIn('Проверете ръчно', draft.verification_note)

    def test_unavailable_verification_leaves_draft_unchecked(self):
        def run_chat(*, model, messages, **kwargs):
            if messages[0]['content'] == prompts.POLL_VERIFY_SYSTEM:
                raise AIUnavailable('Няма връзка.')
            return AIResult(text=json.dumps(QUIZ, ensure_ascii=False), model=model)

        with patch('blog.ai.polls.run_chat', side_effect=run_chat):
            polls.generate_drafts_for_week(MONDAY, self.site_settings)

        self.assertTrue(all(
            draft.verification == PollDraft.Verification.UNCHECKED
            for draft in PollDraft.objects.all()
        ))

    def test_stops_after_six_failed_attempts(self):
        with patch('blog.ai.polls.run_chat', side_effect=fake_model(generation_text='не е JSON')):
            created, failed = polls.generate_drafts_for_week(MONDAY, self.site_settings)
            polls.generate_drafts_for_week(MONDAY, self.site_settings)

        self.assertEqual((created, failed), (0, polls.MAX_ATTEMPTS_PER_WEEK))
        self.assertEqual(PollDraft.objects.count(), polls.MAX_ATTEMPTS_PER_WEEK)

    def test_does_nothing_when_week_already_has_a_poll(self):
        start, end = polls.week_bounds(MONDAY)
        PollQuestion.objects.create(title='Ръчна анкета', start_date=start, end_date=end)

        with patch('blog.ai.polls.run_chat') as run_chat:
            self.assertEqual(polls.generate_drafts_for_week(MONDAY, self.site_settings), (0, 0))
        run_chat.assert_not_called()

    def test_quota_error_stops_generation(self):
        with patch('blog.ai.polls.run_chat', side_effect=AIQuotaExceeded('лимит')):
            with self.assertRaises(AIQuotaExceeded):
                polls.generate_drafts_for_week(MONDAY, self.site_settings)
        self.assertFalse(PollDraft.objects.exists())


class PublishDraftTests(TestCase):
    def test_publish_creates_poll_for_the_whole_week(self):
        draft = make_draft()
        other = make_draft(title='Друг въпрос')

        poll = polls.publish_draft(draft)

        start, end = polls.week_bounds(MONDAY)
        self.assertEqual((poll.start_date, poll.end_date), (start, end))
        self.assertEqual(poll.title, 'Кой е създал Linux?')
        self.assertEqual(
            list(poll.options.values_list('key', 'text', 'is_correct')),
            [('a', 'Линус Торвалдс', True), ('b', 'Бил Гейтс', False), ('c', 'Стив Джобс', False), ('d', 'Денис Ричи', False)],
        )
        draft.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(draft.status, PollDraft.Status.PUBLISHED)
        self.assertEqual(draft.poll, poll)
        self.assertEqual(other.status, PollDraft.Status.NOT_CHOSEN)

    def test_cannot_publish_twice_or_over_existing_poll(self):
        draft = make_draft()
        polls.publish_draft(draft)
        with self.assertRaises(polls.PollPublishError):
            polls.publish_draft(draft)
        with self.assertRaises(polls.PollPublishError):
            polls.publish_draft(make_draft())
        self.assertEqual(PollQuestion.objects.count(), 1)

    def test_auto_publish_picks_first_confirmed_draft(self):
        make_draft(title='Спорен', verification=PollDraft.Verification.DISPUTED)
        confirmed = make_draft(title='Потвърден')

        poll = polls.auto_publish_week(MONDAY)

        self.assertEqual(poll.title, 'Потвърден')
        confirmed.refresh_from_db()
        self.assertEqual(confirmed.status, PollDraft.Status.PUBLISHED)

    def test_auto_publish_skips_week_without_confirmed_draft(self):
        make_draft(verification=PollDraft.Verification.DISPUTED)
        make_draft(verification=PollDraft.Verification.UNCHECKED)

        self.assertIsNone(polls.auto_publish_week(MONDAY))
        self.assertFalse(PollQuestion.objects.exists())


@override_settings(**AI_KEYS)
class WeeklyScheduleTests(TestCase):
    def test_thursday_generates_drafts_for_next_week(self):
        site_settings = enable_poll_ai()
        with patch('blog.ai.polls.run_chat', side_effect=fake_model()):
            report = polls.run_weekly_poll_tasks(site_settings, today=THURSDAY)

        self.assertEqual(report['created'], 3)
        self.assertEqual(set(PollDraft.objects.values_list('week_start', flat=True)), {MONDAY})

    def test_wednesday_generates_nothing(self):
        site_settings = enable_poll_ai()
        with patch('blog.ai.polls.run_chat') as run_chat:
            polls.run_weekly_poll_tasks(site_settings, today=WEDNESDAY)
        run_chat.assert_not_called()

    def test_monday_auto_publishes(self):
        site_settings = enable_poll_ai()
        make_draft()
        report = polls.run_weekly_poll_tasks(site_settings, today=MONDAY)
        self.assertIsNotNone(report['published'])

    def test_auto_publish_switch_off_keeps_drafts(self):
        site_settings = enable_poll_ai(ai_poll_auto_publish=False)
        make_draft()
        polls.run_weekly_poll_tasks(site_settings, today=MONDAY)
        self.assertFalse(PollQuestion.objects.exists())

    def test_main_ai_switch_off_does_nothing(self):
        site_settings = enable_poll_ai(enable_ai=False)
        make_draft()
        with patch('blog.ai.polls.run_chat') as run_chat:
            polls.run_weekly_poll_tasks(site_settings, today=THURSDAY)
            polls.run_weekly_poll_tasks(site_settings, today=MONDAY)
        run_chat.assert_not_called()
        self.assertFalse(PollQuestion.objects.exists())

    def test_command_runs_poll_task(self):
        enable_poll_ai(enable_ai_poll_drafts=True)
        out = StringIO()
        with patch('blog.ai.polls.run_weekly_poll_tasks', return_value={'published': None, 'created': 2, 'failed': 1}) as task:
            with patch('blog.management.commands.run_ai_tasks.run_weekly_poll_tasks', task):
                call_command('run_ai_tasks', only='polls', stdout=out)
        self.assertIn('нови чернови 2, неуспешни 1', out.getvalue())


class PollDraftAdminTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('admin', 'admin@example.com', 'pass')
        self.client.force_login(self.admin)

    def _form(self, draft, **overrides):
        data = {
            'title': draft.title,
            'task_description': draft.task_description,
            'option_a': draft.option_a,
            'option_b': draft.option_b,
            'option_c': draft.option_c,
            'option_d': draft.option_d,
            'correct_key': draft.correct_key,
        }
        data.update(overrides)
        return data

    def test_change_page_shows_publish_button(self):
        draft = make_draft()
        response = self.client.get(reverse('admin:blog_polldraft_change', args=[draft.pk]))
        self.assertContains(response, 'Запази и избери тази анкета')

    def test_publish_button_saves_edits_and_schedules_poll(self):
        draft = make_draft()
        data = self._form(draft, title='Поправено заглавие', correct_key='b')
        data['blog_polldraft_publish_this_draft'] = '1'

        response = self.client.post(reverse('admin:blog_polldraft_change', args=[draft.pk]), data, follow=True)

        self.assertContains(response, 'Готово!')
        poll = PollQuestion.objects.get()
        self.assertEqual(poll.title, 'Поправено заглавие')
        self.assertEqual(PollOption.objects.get(question=poll, is_correct=True).key, 'b')

    def test_published_draft_is_read_only(self):
        draft = make_draft()
        polls.publish_draft(draft)
        response = self.client.get(reverse('admin:blog_polldraft_change', args=[draft.pk]))
        self.assertNotContains(response, 'Запази и избери тази анкета')
        self.assertNotContains(response, 'name="title"')

    def test_changelist_loads(self):
        make_draft()
        make_draft(status=PollDraft.Status.FAILED, title='', option_a='', verification_note='Невалиден JSON.')
        response = self.client.get(reverse('admin:blog_polldraft_changelist'))
        self.assertContains(response, 'Неуспешен опит: Невалиден JSON.')
