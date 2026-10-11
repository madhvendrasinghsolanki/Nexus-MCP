"""Append-only audit trail for write operations.

Records WHAT happened (tool, source, operation, target id, dry-run flag, outcome), never the
data written. For real writes an *intent* record is persisted BEFORE the write runs; if it cannot
be stored the write is refused (fail closed), so there is no unaudited mutation.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime
from typing import Any

from nexus_mcp.core.config import Settings
from nexus_mcp.core.errors import NexusError
from nexus_mcp.core.logging import get_logger

log = get_logger("audit")


class AuditUnavailable(NexusError):
    """The audit sink is configured but cannot be written."""


class AuditLog:
    def __init__(self, path: str | None = None) -> None:
        self._path = path
        self._lock = threading.Lock()

    @classmethod
    def from_settings(cls, s: Settings) -> AuditLog:
        return cls(s.audit_log_path)

    def _persist(self, record: dict[str, Any]) -> None:
        log.info("audit", **record)
        if not self._path:
            return
        line = json.dumps(record, separators=(",", ":"), default=str) + "\n"
        try:
            with self._lock:
                fd = os.open(self._path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
                try:
                    os.write(fd, line.encode())
                    os.fsync(fd)
                finally:
                    os.close(fd)
        except OSError:
            raise AuditUnavailable(
                "The audit log is unavailable, so this write was refused.",
                hint="The operator must fix NEXUS_AUDIT_LOG_PATH permissions or disk space.",
            ) from None

    def record(
        self, phase: str, *, tool: str, source: str, operation: str, target: str | None,
        dry_run: bool, outcome: str | None = None, error_code: str | None = None,
        affected: int | None = None,
    ) -> None:  # fmt: skip
        self._persist(
            {
                "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
                "phase": phase,  # intent | result
                "tool": tool,
                "source": source,
                "operation": operation,
                "target": target,
                "dry_run": dry_run,
                "outcome": outcome,  # ok | denied | error
                "error_code": error_code,
                "affected": affected,
            }
        )
