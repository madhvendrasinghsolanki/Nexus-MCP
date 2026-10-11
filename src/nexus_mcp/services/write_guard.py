"""Central policy for mutating operations. Every write path calls this before touching a source."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import Literal

from nexus_mcp.core.config import Settings
from nexus_mcp.core.errors import ConfirmationRequired, WriteNotPermitted

Operation = Literal["insert", "update", "delete"]


@dataclass(frozen=True)
class WritePolicy:
    allow_writes: bool
    allow_deletes: bool
    writable: frozenset[str]
    max_rows: int
    scope_noun: str = "table"
    scope_env: str = "SUPABASE_WRITABLE_TABLES"

    @classmethod
    def for_supabase(cls, s: Settings) -> WritePolicy:
        return cls(s.allow_writes, s.allow_deletes, s.supabase_writable_table_set, s.max_write_rows)

    @classmethod
    def for_notion(cls, s: Settings) -> WritePolicy:
        return cls(
            s.allow_writes,
            s.allow_deletes,
            s.notion_writable_set,
            s.max_write_rows,
            scope_noun="parent",
            scope_env="NOTION_WRITABLE_PARENTS",
        )

    @property
    def can_write(self) -> bool:
        return self.allow_writes and bool(self.writable)

    def check(self, op: Operation, scope: str | Collection[str]) -> None:
        """`scope` is a table name, or any of several identifiers that would authorise the write."""
        candidates = [scope] if isinstance(scope, str) else list(scope)
        if not self.allow_writes:
            raise WriteNotPermitted(
                "Write operations are disabled on this server.",
                hint="The operator must set NEXUS_ALLOW_WRITES=true.",
            )
        if op == "delete" and not self.allow_deletes:
            raise WriteNotPermitted(
                "Delete operations are disabled on this server.",
                hint="The operator must set NEXUS_ALLOW_DELETES=true.",
            )
        if not any(c in self.writable for c in candidates):
            label = candidates[0] if candidates else "?"
            raise WriteNotPermitted(
                f"The {self.scope_noun} '{label}' is not writable.",
                hint=f"The operator must list it in {self.scope_env}.",
            )

    def check_row_budget(self, count: int) -> None:
        if count > self.max_rows:
            raise WriteNotPermitted(
                f"This would affect {count} rows; the limit is {self.max_rows}.",
                hint="Narrow the filters, or the operator can raise NEXUS_MAX_WRITE_ROWS.",
            )

    @staticmethod
    def check_confirmation(expected: int | None, actual: int) -> None:
        """Destructive writes must echo the row count seen in a dry run."""
        if expected is None:
            raise ConfirmationRequired(
                "Set expected_count to the matched_count returned by a dry run.",
                hint="Call once with dry_run=true, review it, then repeat with dry_run=false.",
            )
        if expected != actual:
            raise ConfirmationRequired(
                f"expected_count={expected} but {actual} rows match now. Nothing was changed.",
                hint="Re-run the dry run and confirm again.",
            )

    @staticmethod
    def check_version(expected: str | None, actual: str) -> None:
        """Optimistic concurrency: the caller must echo the last_edited_time from a dry run."""
        if not expected:
            raise ConfirmationRequired(
                "Set expected_last_edited_time to the value returned by a dry run.",
                hint="Call once with dry_run=true, review it, then repeat with dry_run=false.",
            )
        if expected != actual:
            raise ConfirmationRequired(
                "The page changed since the dry run. Nothing was changed.",
                hint="Re-run the dry run and confirm again.",
            )
