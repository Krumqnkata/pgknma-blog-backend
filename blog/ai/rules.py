"""Ready answers written by admins (AssistantRule) and the log of unanswered questions."""

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from blog.models import AssistantRule, AssistantUnansweredQuestion

from .intents import RuleAnswer
from .text import contains_stem, normalize, stems


def _phrase_stems(phrase):
    return stems(phrase)


def match_admin_rule(question):
    """The best active admin rule for the question, as a RuleAnswer, or None.

    A phrase matches when all of its (stemmed) words are in the question. The
    most specific phrase wins; on equal specificity the higher priority wins.
    """
    question_stems = stems(question)
    if not question_stems:
        return None
    best = None
    for rule in AssistantRule.objects.filter(is_active=True):
        for line in (rule.phrases or '').splitlines():
            wanted = _phrase_stems(line)
            if not wanted or not all(contains_stem(question_stems, item) for item in wanted):
                continue
            key = (rule.priority, len(wanted))
            if best is None or key > best[0]:
                best = (key, rule)
    if best is None:
        return None
    rule = best[1]
    AssistantRule.objects.filter(pk=rule.pk).update(hit_count=F('hit_count') + 1, last_hit_at=timezone.now())
    sources = []
    if rule.link_url:
        sources.append({'title': rule.link_title or rule.title, 'url': rule.link_url})
    return RuleAnswer(rule.answer.strip(), f'admin:{rule.pk}', sources)


def log_unanswered(question):
    """Count a question nobody could answer. No user data is stored."""
    question = (question or '').strip()[:500]
    normalized = normalize(question)[:500]
    if not normalized:
        return
    updated = AssistantUnansweredQuestion.objects.filter(normalized=normalized).update(
        times_asked=F('times_asked') + 1,
        last_asked_at=timezone.now(),
        status=AssistantUnansweredQuestion.STATUS_NEW,
    )
    if updated:
        return
    try:
        with transaction.atomic():
            AssistantUnansweredQuestion.objects.create(question=question, normalized=normalized)
    except IntegrityError:
        AssistantUnansweredQuestion.objects.filter(normalized=normalized).update(times_asked=F('times_asked') + 1)
