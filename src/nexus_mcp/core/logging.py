"""Structured stderr logging (stdout is the MCP stdio wire) with secret redaction."""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import MutableMapping
from typing import Any, TextIO, cast

import structlog

_SENSITIVE_KEYS = re.compile(r"(token|secret|key|password|authorization|apikey)", re.I)
_SECRET_VALUE = re.compile(
    r"(secret_[A-Za-z0-9]{8,}|ntn_[A-Za-z0-9]{8,}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_.-]+)"
)


def _redact(_: Any, __: str, event_dict: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    for key, value in list(event_dict.items()):
        if _SENSITIVE_KEYS.search(key):
            event_dict[key] = "***"
        elif isinstance(value, str):
            event_dict[key] = _SECRET_VALUE.sub("***", value)
    return event_dict


class _LiveStderr:
    """File-like proxy that resolves `sys.stderr` at write time (robust to stream swaps)."""

    def write(self, data: str) -> int:
        return sys.stderr.write(data)

    def flush(self) -> None:
        sys.stderr.flush()


def configure_logging(level: str = "INFO", fmt: str = "console") -> None:
    logging.basicConfig(stream=sys.stderr, level=level, format="%(message)s", force=True)
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)  # request URLs contain user query data
    renderer: Any = (
        structlog.processors.JSONRenderer()
        if fmt == "json"
        else structlog.dev.ConsoleRenderer(colors=False)
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _redact,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
        logger_factory=structlog.PrintLoggerFactory(file=cast(TextIO, _LiveStderr())),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[no-any-return]


# Safe default for embedders: if nobody configured structlog, log to stderr (never stdout, which
# carries the MCP stdio protocol). The CLI reconfigures with the user's level/format.
if not structlog.is_configured():
    configure_logging()
