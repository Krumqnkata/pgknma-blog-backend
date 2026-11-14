"""Finds the knowledge chunks that best match a question.

Hybrid ranking: semantic similarity of bge-m3 embeddings (when available)
plus overlap of word stems. Without embeddings (no keys, quota used up) it
falls back to the stem overlap alone.
"""

import logging
import math
import operator
from dataclasses import dataclass

from django.db.models import Count, Max

from blog.models import KnowledgeChunk

from .client import EMBEDDING_MODEL, AIError, run_embeddings
from .knowledge_index import unpack
from .text import stem_matches, stems

logger = logging.getLogger(__name__)

SEMANTIC_WEIGHT = 0.65
KEYWORD_WEIGHT = 0.35
MIN_SCORE = 0.32
MIN_KEYWORD_ONLY = 0.34

_cache = {'key': None, 'chunks': []}


@dataclass
class Hit:
    chunk_id: int
    source_type: str
    title: str
    url: str
    text: str
    score: float


def _load_chunks():
    """All chunks with decoded vectors, cached until the index changes."""
    state = KnowledgeChunk.objects.aggregate(count=Count('pk'), changed=Max('updated_at'))
    key = (state['count'], state['changed'])
    if _cache['key'] != key:
        chunks = []
        for row in KnowledgeChunk.objects.values('pk', 'source_type', 'title', 'url', 'text', 'stems', 'embedding', 'embedding_model'):
            vector = None
            if row['embedding'] and row['embedding_model'] == EMBEDDING_MODEL:
                vector = unpack(row['embedding'])
                norm = math.sqrt(sum(value * value for value in vector)) or 1.0
                vector = (vector, norm)
            chunks.append({
                **row,
                'stem_set': set((row['stems'] or '').split()),
                'title_stems': stems(row['title']),
                'vector': vector,
            })
        _cache['key'] = key
        _cache['chunks'] = chunks
    return _cache['chunks']


def _keyword_score(question_stems, chunk):
    if not question_stems:
        return 0.0
    stem_set = chunk['stem_set']
    matched = 0
    for wanted in question_stems:
        if wanted in stem_set or any(stem_matches(wanted, have) for have in stem_set if have[:2] == wanted[:2]):
            matched += 1
    title_bonus = 0.15 if any(
        stem_matches(wanted, title_stem) for wanted in question_stems for title_stem in chunk['title_stems']
    ) else 0.0
    return min(1.0, matched / len(question_stems) + title_bonus)


def _cosine(query, chunk):
    vector, norm = chunk['vector']
    query_vector, query_norm = query
    return sum(map(operator.mul, query_vector, vector)) / (norm * query_norm)


def search(question, *, limit=6, use_embeddings=True):
    chunks = _load_chunks()
    if not chunks:
        return []
    question_stems = stems(question)
    query = None
    if use_embeddings and any(chunk['vector'] for chunk in chunks):
        try:
            vector = run_embeddings([question])[0]
            query = (vector, math.sqrt(sum(value * value for value in vector)) or 1.0)
        except AIError as exc:
            logger.info('Question embedding unavailable, using keywords only: %s', exc)

    hits = []
    for chunk in chunks:
        keyword = _keyword_score(question_stems, chunk)
        if query is not None and chunk['vector']:
            score = SEMANTIC_WEIGHT * _cosine(query, chunk) + KEYWORD_WEIGHT * keyword
            threshold = MIN_SCORE
        else:
            score, threshold = keyword, MIN_KEYWORD_ONLY
        if score >= threshold:
            hits.append(Hit(chunk['pk'], chunk['source_type'], chunk['title'], chunk['url'], chunk['text'], score))
    hits.sort(key=lambda hit: hit.score, reverse=True)
    return hits[:limit]


SOURCE_SCORE_RATIO = 0.85


def build_context(hits, *, max_chars=6000):
    """Context text for the model and the list of sources (unique pages).

    Every hit goes to the model, but only hits close to the best one are shown
    as sources, so visitors do not get unrelated links.
    """
    blocks, sources, seen_urls, used = [], [], set(), 0
    best = hits[0].score if hits else 0
    for hit in hits:
        block = f'### {hit.title}' + (f' ({hit.url})' if hit.url else '') + f'\n{hit.text}'
        if used + len(block) > max_chars:
            break
        blocks.append(block)
        used += len(block)
        if hit.url and hit.url not in seen_urls and hit.score >= best * SOURCE_SCORE_RATIO:
            seen_urls.add(hit.url)
            sources.append({'title': hit.title.replace(' (продължение)', ''), 'url': hit.url})
    return '\n\n'.join(blocks), sources[:3]
