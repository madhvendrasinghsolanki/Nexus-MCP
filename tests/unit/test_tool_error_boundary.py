"""safe_tool / guarded: unexpected failures never leak details to the client."""

from __future__ import annotations

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from nexus_mcp.core.config import Settings
from nexus_mcp.core.errors import ValidationFailed
from nexus_mcp.core.ratelimit import RateLimiter
from nexus_mcp.providers.registry import ProviderRegistry
from nexus_mcp.services.audit import AuditLog
from nexus_mcp.tools._common import App, Audit, guarded, safe_tool


class RecordingAudit(AuditLog):
    def __init__(self) -> None:
        super().__init__(None)
        self.records: list[tuple[str, dict[str, object]]] = []

    def record(self, kind: str, **fields: object) -> None:  # type: ignore[override]
        self.records.append((kind, fields))


def _app() -> tuple[App, RecordingAudit]:
    audit = RecordingAudit()
    app = App(Settings(NEXUS_ALLOW_WRITES=True), ProviderRegistry(), RateLimiter(100, 100), audit)
    return app, audit


async def test_unexpected_exception_is_masked() -> None:
    @safe_tool
    async def boom() -> None:
        raise RuntimeError("password=hunter2 at db.internal")

    with pytest.raises(ToolError) as ei:
        await boom()
    assert str(ei.value) == "[internal_error] The tool failed unexpectedly."


async def test_tool_error_passes_through_unchanged() -> None:
    @safe_tool
    async def f() -> None:
        raise ToolError("already shaped")

    with pytest.raises(ToolError, match="already shaped"):
        await f()


async def test_write_unexpected_error_is_audited_and_masked() -> None:
    app, audit = _app()
    records = audit.records

    @guarded(app, Audit("supabase", "insert", "table"))
    async def insert_rows(table: str, dry_run: bool = True) -> None:
        raise RuntimeError("secret internals")

    with pytest.raises(ToolError) as ei:
        await insert_rows(table="notes", dry_run=False)
    assert "secret" not in str(ei.value)
    assert [k for k, _ in records] == ["intent", "result"]
    assert records[-1][1]["outcome"] == "error"
    assert records[-1][1]["error_code"] == "internal_error"


async def test_write_validation_error_is_audited_as_error() -> None:
    app, audit = _app()

    @guarded(app, Audit("supabase", "insert", "table"))
    async def insert_rows(table: str, dry_run: bool = True) -> None:
        raise ValidationFailed("bad input")

    with pytest.raises(ToolError, match="bad input"):
        await insert_rows(table="notes", dry_run=True)
    assert audit.records[-1][1]["outcome"] == "error"
