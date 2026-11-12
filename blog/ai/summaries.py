"""„Накратко“: short AI summaries of public articles."""

import logging
import re

from django.db.models import Q
from django.utils import timezone
from django.utils.html import strip_tags

from blog.models import Posts

from . import config, prompts
from .client import AIQuotaExceeded, AIUnavailable, run_chat

logger = logging.getLogger(__name__)

MAX_SOURCE_CHARS = 12_000
MAX_SUMMARY_CHARS = 700

_MARKDOWN_IMAGE = re.compile(r'!\[[^\]]*\]\([^)]*\)')
_MARKDOWN_LINK = re.compile(r'\[([^\]]+)\]\([^)]*\)')
_MARKDOWN_SYNTAX = re.compile(r'(^|\n)\s{0,3}(#{1,6}|>|[-*+]|\d+\.)\s+')
_EXTRA_SPACE = re.compile(r'[ \t]+')


def markdown_to_text(markdown):
    text = _MARKDOWN_IMAGE.sub('', markdown or '')
    text = _MARKDOWN_LINK.sub(r'\1', text)
    text = _MARKDOWN_SYNTAX.sub(r'\1', text)
    text = strip_tags(text).replace('**', '').replace('__', '').replace('`', '')
    return _EXTRA_SPACE.sub(' ', text).strip()


def public_posts_needing_summary():
    """Public posts whose text changed since the last summary, not edited by hand."""
    now = timezone.now()
    return (
        Posts.objects.filter(published=True, allowed=True, ai_summary_manual=False)
        .filter(Q(published_at__isnull=True) | Q(published_at__lte=now))
        .order_by('-created_at')
    )


def post_needs_summary(post):
    return not post.ai_summary_manual and post.ai_summary_source_hash != post.ai_summary_source_digest()


def _clean_summary(text):
    text = text.strip().strip('"„“”').strip()
    if text.lower().startswith('накратко:'):
        text = text[len('накратко:'):].strip()
    if len(text) > MAX_SUMMARY_CHARS:
        text = text[:MAX_SUMMARY_CHARS].rsplit(' ', 1)[0].rstrip(',;:') + '…'
    return text


def generate_summary(post):
    """Create and store the summary of one post. Raises AIError on failure."""
    source = markdown_to_text(post.content)[:MAX_SOURCE_CHARS]
    user_message = (
        f"Заглавие: {post.title}\n"
        f"Въведение: {post.hook}\n\n"
        f"Статия:\n{source}"
    )
    result = run_chat(
        model=config.public_model(),
        messages=[
            {'role': 'system', 'content': prompts.SUMMARY_SYSTEM},
            {'role': 'user', 'content': user_message},
        ],
        max_tokens=300,
        temperature=0.3,
    )
    post.ai_summary = _clean_summary(result.text)
    post.ai_summary_source_hash = post.ai_summary_source_digest()
    post.ai_summary_updated_at = timezone.now()
    post.ai_summary_manual = False
    # update() instead of save(): no post_save signals, so a new summary never
    # re-sends "new post" notifications.
    Posts.objects.filter(pk=post.pk).update(
        ai_summary=post.ai_summary,
        ai_summary_source_hash=post.ai_summary_source_hash,
        ai_summary_updated_at=post.ai_summary_updated_at,
        ai_summary_manual=False,
    )
    return post.ai_summary


def summarize_pending_posts(limit):
    """Summarize up to `limit` posts. AIQuotaExceeded propagates to stop the run."""
    done = failed = 0
    for post in public_posts_needing_summary().iterator():
        if done + failed >= limit:
            break
        if not post_needs_summary(post):
            continue
        try:
            generate_summary(post)
            done += 1
        except AIQuotaExceeded:
            raise
        except AIUnavailable as exc:
            failed += 1
            logger.warning('AI summary failed for post %s: %s', post.pk, exc)
    return done, failed
