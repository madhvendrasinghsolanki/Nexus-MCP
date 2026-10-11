"""Property-based tests (hypothesis) for the security-critical pure functions."""

from __future__ import annotations

import contextlib
import json
import re

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from nexus_mcp.core.errors import ValidationFailed
from nexus_mcp.core.models import KnowledgeItem
from nexus_mcp.core.ratelimit import TokenBucket
from nexus_mcp.core.sanitize import clip_json, sanitize_json, sanitize_text
from nexus_mcp.core.validation import check_payload, parse_notion_id, validate_identifier
from nexus_mcp.providers.notion.convert import text_to_blocks
from nexus_mcp.providers.supabase.filters import Filter, FilterOp, escape_like_term, quote
from nexus_mcp.services.ranking import rank

PROFILE = settings(max_examples=150, deadline=None)

text = st.text(max_size=60)
json_leaf = st.none() | st.booleans() | st.integers(-(10**6), 10**6) | text
json_value = st.recursive(
    json_leaf,
    lambda c: st.lists(c, max_size=4) | st.dictionaries(text, c, max_size=4),
    max_leaves=15,
)


def _unquote_list(body: str) -> list[str]:
    """Parse '"a","b\\"c"' back into values, like PostgREST would."""
    out: list[str] = []
    i = 0
    while i < len(body):
        assert body[i] == '"', f"unquoted element at {i}: {body!r}"
        i += 1
        buf = []
        while body[i] != '"':
            if body[i] == "\\":
                i += 1
            buf.append(body[i])
            i += 1
        i += 1
        out.append("".join(buf))
        if i < len(body):
            assert body[i] == ","
            i += 1
    return out


# --- filters -------------------------------------------------------------------------------


@PROFILE
@given(st.lists(text, min_size=1, max_size=10))
def test_in_filter_values_round_trip_and_cannot_break_out(values: list[str]) -> None:
    key, val = Filter(column="status", op=FilterOp.IN, value=values).to_param()
    assert key == "status"
    assert val.startswith("in.(") and val.endswith(")")
    assert _unquote_list(val[4:-1]) == values  # injection chars stay inside the quotes


@PROFILE
@given(text)
def test_quote_is_always_one_balanced_token(value: str) -> None:
    assert _unquote_list(quote(value)) == [value]


@PROFILE
@given(st.text(max_size=40))
def test_filter_column_only_accepts_identifiers(col: str) -> None:
    try:
        f = Filter(column=col, op=FilterOp.EQ, value=1)
    except ValueError:
        return
    assert re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", f.to_param()[0])


@PROFILE
@given(text)
def test_escape_like_term_leaves_no_unescaped_wildcards(term: str) -> None:
    out = escape_like_term(term)
    assert "*" not in out
    assert not re.search(r"(?<!\\)[%_]", re.sub(r"\\\\", "", out))


# --- sanitize ------------------------------------------------------------------------------


@PROFILE
@given(st.text(max_size=200))
def test_sanitize_text_is_idempotent(s: str) -> None:
    once = sanitize_text(s)
    assert sanitize_text(once) == once
    assert len(once) <= len(s)


@PROFILE
@given(json_value)
def test_sanitize_json_is_idempotent_and_json_safe(v: object) -> None:
    once = sanitize_json(v)
    assert sanitize_json(once) == once
    json.dumps(once)


@PROFILE
@given(json_value, st.integers(1, 20))
def test_clip_json_bounds_every_string(v: object, limit: int) -> None:
    clipped, n = clip_json(v, limit)
    assert n >= 0

    def walk(x: object) -> None:
        if isinstance(x, str):
            assert len(x) <= limit + 40  # the marker suffix
        elif isinstance(x, dict):
            for y in x.values():
                walk(y)
        elif isinstance(x, list):
            for y in x:
                walk(y)

    walk(clipped)


# --- validation ----------------------------------------------------------------------------


@PROFILE
@given(st.uuids())
def test_parse_notion_id_round_trips_every_format(u: object) -> None:
    canonical = str(u)
    assert parse_notion_id(canonical) == canonical
    assert parse_notion_id(canonical.replace("-", "").upper()) == canonical
    assert parse_notion_id(f"https://www.notion.so/My-Page-Title-{canonical.replace('-', '')}") == (
        canonical
    )


@PROFILE
@given(st.text(max_size=80))
def test_parse_notion_id_never_returns_garbage(raw: str) -> None:
    try:
        out = parse_notion_id(raw)
    except ValidationFailed:
        return
    assert re.fullmatch(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", out)


@PROFILE
@given(st.text(max_size=80))
def test_validate_identifier_accepts_only_safe_names(raw: str) -> None:
    try:
        validate_identifier(raw)
    except ValidationFailed:
        return
    assert re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", raw)


@PROFILE
@given(json_value, st.integers(1_000, 5_000))
def test_check_payload_never_raises_anything_but_validation_failed(v: object, cap: int) -> None:
    with contextlib.suppress(ValidationFailed):
        check_payload(v, cap)


def test_check_payload_rejects_deep_nesting() -> None:
    deep: object = 1
    for _ in range(20):
        deep = [deep]
    with pytest.raises(ValidationFailed):
        check_payload(deep, 100_000)


# --- ranking -------------------------------------------------------------------------------

items = st.lists(
    st.builds(
        KnowledgeItem,
        source=st.sampled_from(["a", "b"]),
        id=st.text(alphabet="abc123", min_size=1, max_size=3),
        title=text,
        snippet=st.none() | text,
    ),
    max_size=12,
)


@PROFILE
@given(text, items)
def test_rank_is_deterministic_bounded_and_deduplicated(
    query: str, xs: list[KnowledgeItem]
) -> None:
    a, b = rank(query, xs), rank(query, xs)
    assert [(i.source, i.id) for i in a] == [(i.source, i.id) for i in b]
    keys = [(i.source, i.id) for i in a]
    assert len(keys) == len(set(keys))
    scores = [i.score for i in a]
    assert all(s is not None and 0 <= s <= 1 for s in scores)
    assert scores == sorted(scores, reverse=True)  # type: ignore[type-var]


# --- rate limit ----------------------------------------------------------------------------


@PROFILE
@given(st.integers(1, 200), st.lists(st.floats(0, 120, allow_nan=False), max_size=40))
def test_token_bucket_never_exceeds_capacity(per_min: int, gaps: list[float]) -> None:
    now = 0.0
    bucket = TokenBucket(per_min, clock=lambda: now)
    granted = 0
    for gap in gaps:
        now += gap
        if bucket.take() == 0.0:
            granted += 1
    # total grants can never exceed initial capacity + refill over the elapsed time
    assert granted <= per_min + now * per_min / 60.0 + 1e-6


# --- notion conversion ---------------------------------------------------------------------


@PROFILE
@given(st.text(max_size=500))
def test_text_to_blocks_never_crashes_and_emits_valid_blocks(content: str) -> None:
    for block in text_to_blocks(content):
        assert block["object"] == "block"
        assert block["type"] in block
