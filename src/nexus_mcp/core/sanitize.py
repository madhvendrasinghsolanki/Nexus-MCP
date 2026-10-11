"""Hardening for text that originates in external systems (Notion pages, database cells).

Everything a provider returns is attacker-influenced data. This module removes characters that are
invisible to humans but readable by models (a known prompt-injection carrier), bounds sizes, and
offers a *heuristic* injection scan. None of this makes retrieved text trustworthy; it removes the
cheapest tricks and gives clients signals. Authority still comes only from the user.
"""

from __future__ import annotations

import re
from typing import Annotated, Any

from pydantic import AfterValidator

# Invisible / control characters that carry no legitimate meaning in knowledge text.
_INVISIBLE = re.compile(
    "["
    "\\x00-\\x08\\x0b\\x0c\\x0e-\\x1f\\x7f"  # C0 controls except \\t \\n \\r
    "\\u00ad"  # soft hyphen
    "\\u200b\\u200c\\u200e\\u200f"  # zero-width space/non-joiner, LRM, RLM (ZWJ kept: emoji)
    "\\u2028\\u2029"  # line / paragraph separators
    "\\u202a-\\u202e"  # bidi embeddings and overrides
    "\\u2060-\\u2064\\u2066-\\u206f"  # word joiner, invisible operators, bidi isolates
    "\\ufeff"  # BOM / zero-width no-break space
    "\\U000e0000-\\U000e007f"  # Unicode "tag" characters (ASCII smuggling)
    "\\U000e0100-\\U000e01ef"  # variation selector supplement (used to smuggle data)
    "]"
)


def sanitize_text(text: str) -> str:
    return _INVISIBLE.sub("", text)


def sanitize_json(value: Any) -> Any:
    """Recursively sanitize every string (and dict key) inside a JSON-like value."""
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, dict):
        return {sanitize_text(str(k)): sanitize_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize_json(v) for v in value]
    return value


def clip_json(value: Any, max_chars: int) -> tuple[Any, int]:
    """Truncate over-long strings anywhere in a JSON-like value. Returns (value, clipped_count)."""
    if isinstance(value, str):
        if len(value) <= max_chars:
            return value, 0
        return value[:max_chars] + f"… [truncated {len(value) - max_chars} chars]", 1
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        total = 0
        for k, v in value.items():
            out[k], n = clip_json(v, max_chars)
            total += n
        return out, total
    if isinstance(value, list):
        items, total = [], 0
        for v in value:
            c, n = clip_json(v, max_chars)
            items.append(c)
            total += n
        return items, total
    return value, 0


# Pydantic field types: sanitized automatically wherever a model is built.
SafeStr = Annotated[str, AfterValidator(sanitize_text)]
SafeDict = Annotated[dict[str, Any], AfterValidator(sanitize_json)]
SafeRows = Annotated[list[dict[str, Any]], AfterValidator(sanitize_json)]


# ---- heuristic injection scan -------------------------------------------------------------------
_PATTERNS: dict[str, re.Pattern[str]] = {
    "instruction_override": re.compile(
        r"(ignore|disregard|forget|override)\s+(all\s+|any\s+|the\s+|your\s+)?"
        r"(previous|prior|above|earlier|preceding|system)\s+(instructions?|prompts?|rules?|messages?)",
        re.I,
    ),
    "addresses_the_ai": re.compile(
        r"(notice|message|note|instructions?)\s+(to|for)\s+(the\s+)?(ai|assistants?|llms?|agents?|models?)",
        re.I,
    ),
    "destructive_command": re.compile(
        r"\b(delete|drop|erase|wipe|destroy|remove)\s+(all|every|everything|each)\b", re.I
    ),
    "concealment": re.compile(
        r"(do\s+not|don'?t|never)\s+(tell|inform|mention|reveal|show)\s+(this\s+)?(to\s+)?the\s+user",
        re.I,
    ),
    "role_tag_spoofing": re.compile(
        r"<\s*/?\s*(system|assistant|developer|tool_result|function_calls?|instructions?)\s*>", re.I
    ),
    "forced_output": re.compile(r"(reply|respond|answer)\s+(only\s+)?with\s+['\"]", re.I),
}


def scan_for_injection(text: str) -> list[str]:
    """Names of suspicious patterns found. Low precision by design: a signal, not a verdict."""
    return [name for name, rx in _PATTERNS.items() if rx.search(text)]
