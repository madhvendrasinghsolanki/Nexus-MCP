"""Transparent lexical ranking for results that come from heterogeneous sources.

Providers return unscored hits (Supabase ILIKE, Notion title search). This module assigns a
comparable 0..1 relevance so results can be merged. It is deliberately simple and explainable.
"""

from __future__ import annotations

import re
from datetime import datetime

from nexus_mcp.core.models import KnowledgeItem

_TOKEN = re.compile(r"\w+", re.UNICODE)

TITLE_WEIGHT = 0.55
SNIPPET_WEIGHT = 0.25
PHRASE_WEIGHT = 0.20
MATCH_FLOOR = 0.05  # a source-reported hit never scores zero


def tokens(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN.findall(text)]


def _coverage(query_tokens: set[str], text_tokens: list[str]) -> float:
    """Fraction of query tokens that prefix-match some token in the text."""
    if not query_tokens:
        return 0.0
    hit = sum(1 for q in query_tokens if any(t.startswith(q) for t in text_tokens))
    return hit / len(query_tokens)


def score_item(query: str, item: KnowledgeItem) -> float:
    if item.score is not None:
        return item.score  # provider-supplied scores are authoritative
    q = set(tokens(query))
    if not q:
        return MATCH_FLOOR
    title_tokens = tokens(item.title)
    snippet_tokens = tokens(item.snippet or "")
    phrase = 1.0 if " ".join(tokens(query)) in " ".join(title_tokens) else 0.0
    # A source that returns no snippet (e.g. Notion title search) must not be capped below one
    # that does, so the unused snippet weight moves to the title.
    title_w = TITLE_WEIGHT + (0.0 if snippet_tokens else SNIPPET_WEIGHT)
    snippet_w = SNIPPET_WEIGHT if snippet_tokens else 0.0
    raw = (
        title_w * _coverage(q, title_tokens)
        + snippet_w * _coverage(q, snippet_tokens)
        + PHRASE_WEIGHT * phrase
    )
    return round(min(max(raw, MATCH_FLOOR), 1.0), 4)


def _recency(item: KnowledgeItem) -> float:
    raw = item.metadata.get("last_edited_time")
    if not isinstance(raw, str):
        return 0.0
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def rank(query: str, items: list[KnowledgeItem]) -> list[KnowledgeItem]:
    """Score, de-duplicate (same URL or same source+id) and sort best-first.

    Ties break on recency, then on the original (provider) order, so output is deterministic.
    """
    scored = [
        (i, item.model_copy(update={"score": score_item(query, item)}))
        for i, item in enumerate(items)
    ]
    best: dict[str, tuple[int, KnowledgeItem]] = {}
    for idx, item in scored:
        key = item.url or f"{item.source}:{item.id}"
        current = best.get(key)
        if current is None or (item.score or 0) > (current[1].score or 0):
            best[key] = (idx, item)
    ordered = sorted(best.values(), key=lambda p: (-(p[1].score or 0), -_recency(p[1]), p[0]))
    return [item for _, item in ordered]
