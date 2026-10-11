"""Input validation primitives shared by config and tools."""

from __future__ import annotations

import json
import re
from urllib.parse import urlparse

from nexus_mcp.core.errors import ValidationFailed

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


def validate_identifier(value: str, what: str = "identifier") -> str:
    """SQL-style identifier: letters, digits, underscore; max 63 chars; no leading digit."""
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValidationFailed(
            f"Invalid {what} {value!r}.",
            hint="Use letters, digits, underscores (max 63 chars, no leading digit).",
        )
    return value


_HEX32 = re.compile(r"[0-9a-fA-F]{32}")
_URL_ID = re.compile(
    r"(?<![0-9a-fA-F])"
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|[0-9a-fA-F]{32})"
    r"(?![0-9a-fA-F])"
)
_NOTION_HOSTS = ("notion.so", "notion.site")


def parse_notion_id(raw: str, what: str = "Notion ID") -> str:
    """Accept a UUID (hyphenated or not) or a notion.so/notion.site URL; return canonical UUID."""
    text = (raw or "").strip()
    if text.lower().startswith(("http://", "https://")):
        parsed = urlparse(text)
        host = parsed.hostname or ""
        if parsed.scheme != "https" or not host.endswith(_NOTION_HOSTS):
            raise ValidationFailed(f"{what} URL must be an https notion.so or notion.site link.")
        # Slugs like "My-Page-<id>" contain hex letters, so match the id on word boundaries.
        matches = _URL_ID.findall(parsed.path)
        compact = matches[-1].replace("-", "") if matches else ""
    else:
        compact = text.replace("-", "")
        if not _HEX32.fullmatch(compact):
            compact = ""
    if not compact:
        raise ValidationFailed(
            f"Invalid {what}.", hint="Pass the 32-character page/database ID or its Notion URL."
        )
    c = compact.lower()
    return f"{c[:8]}-{c[8:12]}-{c[12:16]}-{c[16:20]}-{c[20:]}"


MAX_NESTING = 6


def _depth(value: object, level: int = 0) -> int:
    if level > MAX_NESTING:
        return level
    if isinstance(value, dict):
        return max((_depth(v, level + 1) for v in value.values()), default=level + 1)
    if isinstance(value, list):
        return max((_depth(v, level + 1) for v in value), default=level + 1)
    return level


def check_payload(value: object, max_bytes: int, what: str = "payload") -> None:
    """Bound the size and nesting of caller-supplied JSON before it is sent upstream."""
    try:
        size = len(json.dumps(value, default=str).encode())
    except (TypeError, ValueError):
        raise ValidationFailed(f"The {what} is not valid JSON.") from None
    if size > max_bytes:
        raise ValidationFailed(
            f"The {what} is {size} bytes; the limit is {max_bytes}.",
            hint="Send less data per call.",
        )
    if _depth(value) > MAX_NESTING:
        raise ValidationFailed(f"The {what} is nested deeper than {MAX_NESTING} levels.")
