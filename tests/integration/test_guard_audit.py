"""Rate limiting, audit trail (incl. fail-closed) and tool timeouts, observed over MCP."""

from __future__ import annotations

import asyncio
import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from nexus_mcp.core.config import Settings
from nexus_mcp.providers.registry import ProviderRegistry
from nexus_mcp.providers.supabase import SupabaseSource
from nexus_mcp.server import create_server
from tests.support.fakes import make_fake
from tests.support.postgrest_fake import FakePostgrest, sample_tables

pytestmark = pytest.mark.integration


def build(fake: FakePostgrest, **env: Any):  # type: ignore[no-untyped-def]
    settings = Settings(
        _env_file=None,
        SUPABASE_URL="https://p.supabase.co",
        SUPABASE_KEY="sb-key",
        NEXUS_ALLOW_WRITES=True,
        NEXUS_ALLOW_DELETES=True,
        SUPABASE_WRITABLE_TABLES="notes",
        **env,
    )
    reg = ProviderRegistry()
    reg.register(SupabaseSource(settings, transport=fake.transport()))
    return create_server(settings, reg)


def records(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


async def test_audit_records_intent_and_result_without_data(tmp_path: Path) -> None:
    log = tmp_path / "audit.jsonl"
    fake = FakePostgrest(sample_tables())
    async with Client(build(fake, NEXUS_AUDIT_LOG_PATH=str(log))) as c:
        await c.call_tool(
            "supabase_insert", {"table": "notes", "rows": [{"id": 9, "title": "TOP-SECRET-VALUE"}]}
        )
        await c.call_tool(
            "supabase_insert",
            {"table": "notes", "rows": [{"id": 9, "title": "TOP-SECRET-VALUE"}], "dry_run": False},
        )
    recs = records(log)
    # dry run -> one result record; real write -> intent then result
    assert [(r["phase"], r["dry_run"], r["outcome"]) for r in recs] == [
        ("result", True, "ok"),
        ("intent", False, None),
        ("result", False, "ok"),
    ]
    assert (
        recs[2]["affected"] == 1
        and recs[2]["target"] == "notes"
        and recs[2]["operation"] == "insert"
    )
    assert "TOP-SECRET-VALUE" not in log.read_text()  # what, never the data
    assert stat.S_IMODE(os.stat(log).st_mode) == 0o600


async def test_denied_and_failed_writes_are_audited(tmp_path: Path) -> None:
    log = tmp_path / "a.jsonl"
    async with Client(build(FakePostgrest(sample_tables()), NEXUS_AUDIT_LOG_PATH=str(log))) as c:
        await c.call_tool(
            "supabase_insert", {"table": "secrets", "rows": [{"x": 1}], "dry_run": False}
        )
        await c.call_tool(
            "supabase_delete",
            {"table": "notes", "filters": [{"column": "id", "value": 3}], "dry_run": False},
        )
    results = [r for r in records(log) if r["phase"] == "result"]
    assert [(r["outcome"], r["error_code"]) for r in results] == [
        ("denied", "write_not_permitted"),
        ("denied", "confirmation_required"),
    ]


async def test_write_is_refused_and_not_executed_when_audit_log_is_unwritable(
    tmp_path: Path,
) -> None:
    fake = FakePostgrest(sample_tables())
    bad = tmp_path / "missing-dir" / "audit.jsonl"
    async with Client(build(fake, NEXUS_AUDIT_LOG_PATH=str(bad))) as c:
        res = await c.call_tool(
            "supabase_insert", {"table": "notes", "rows": [{"id": 9}], "dry_run": False}
        )
        dry = await c.call_tool("supabase_insert", {"table": "notes", "rows": [{"id": 9}]})
    assert res.is_error and "audit log is unavailable" in res.content[0].text
    assert not [r for r in fake.requests if r.method == "POST"]  # fail closed: nothing was written
    assert len(fake.tables["notes"]) == 3
    assert not dry.is_error  # a dry run mutates nothing, so it does not need the audit sink


async def test_read_rate_limit_enforced_with_clear_error() -> None:
    async with Client(build(FakePostgrest(sample_tables()), NEXUS_RATE_LIMIT_READS_PER_MIN=2)) as c:
        ok = [await c.call_tool("server_info", {}) for _ in range(2)]
        limited = await c.call_tool("server_info", {})
    assert all(not r.is_error for r in ok)
    assert limited.is_error and "[rate_limited]" in limited.content[0].text
    assert "Retry in" in limited.content[0].text


async def test_write_rate_limit_is_separate_and_applies_to_dry_runs() -> None:
    server = build(FakePostgrest(sample_tables()), NEXUS_RATE_LIMIT_WRITES_PER_MIN=1)
    async with Client(server) as c:
        first = await c.call_tool("supabase_insert", {"table": "notes", "rows": [{"id": 9}]})
        second = await c.call_tool("supabase_insert", {"table": "notes", "rows": [{"id": 9}]})
        read = await c.call_tool("server_info", {})
    assert not first.is_error and second.is_error and "[rate_limited]" in second.content[0].text
    assert not read.is_error


async def test_read_tool_timeout_is_reported() -> None:
    settings = Settings(_env_file=None, NEXUS_TOOL_TIMEOUT_S=0.05, NEXUS_REQUEST_TIMEOUT_S=30)
    reg = ProviderRegistry()
    reg.register(make_fake("slow", delay=5))
    async with Client(create_server(settings, reg)) as c:
        res = await asyncio.wait_for(c.call_tool("search_knowledge", {"query": "x"}), timeout=10)
    assert res.is_error and "[upstream_timeout]" in res.content[0].text
