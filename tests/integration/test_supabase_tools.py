"""End-to-end: real MCP client -> server -> Supabase provider -> fake PostgREST."""

from __future__ import annotations

from typing import Any

import pytest
from mcp import Client

from nexus_mcp.core.config import Settings
from nexus_mcp.providers.registry import ProviderRegistry
from nexus_mcp.providers.supabase import SupabaseSource
from nexus_mcp.server import create_server
from tests.support.postgrest_fake import FakePostgrest, sample_tables

pytestmark = pytest.mark.integration


def build(fake: FakePostgrest, **env: Any):  # type: ignore[no-untyped-def]
    settings = Settings(
        _env_file=None, SUPABASE_URL="https://p.supabase.co", SUPABASE_KEY="sb-secret-xyz123", **env
    )
    reg = ProviderRegistry()
    reg.register(SupabaseSource(settings, transport=fake.transport()))
    return create_server(settings, reg)


async def tool_names(server) -> set[str]:  # type: ignore[no-untyped-def]
    async with Client(server) as c:
        return {t.name for t in (await c.list_tools()).tools}


async def test_tool_visibility_follows_policy() -> None:
    fake = FakePostgrest(sample_tables())
    ro = await tool_names(build(fake))
    assert {"supabase_list_tables", "supabase_describe_table", "supabase_query"} <= ro
    assert not {"supabase_insert", "supabase_update", "supabase_delete"} & ro
    rw = await tool_names(build(fake, NEXUS_ALLOW_WRITES=True))
    assert {"supabase_insert", "supabase_update"} <= rw and "supabase_delete" not in rw
    full = await tool_names(build(fake, NEXUS_ALLOW_WRITES=True, NEXUS_ALLOW_DELETES=True))
    assert "supabase_delete" in full


async def test_no_supabase_tools_when_unconfigured(settings) -> None:  # type: ignore[no-untyped-def]
    names = await tool_names(create_server(settings, ProviderRegistry()))
    assert not any(n.startswith("supabase_") for n in names)


async def test_query_tool_structured_output() -> None:
    server = build(FakePostgrest(sample_tables()))
    async with Client(server) as c:
        res = await c.call_tool(
            "supabase_query",
            {
                "table": "notes",
                "filters": [{"column": "status", "op": "eq", "value": "open"}],
                "limit": 1,
            },
        )
    data = res.structured_content
    assert not res.is_error and data["returned"] == 1 and data["has_more"] is True


async def test_invalid_filter_rejected_at_schema_level() -> None:
    server = build(FakePostgrest(sample_tables()))
    async with Client(server) as c:
        res = await c.call_tool(
            "supabase_query", {"table": "notes", "filters": [{"column": "id&select", "value": 1}]}
        )
    assert res.is_error


async def test_error_text_is_coded_and_secret_free() -> None:
    fake = FakePostgrest(sample_tables())
    server = build(fake)
    async with Client(server) as c:
        res = await c.call_tool("supabase_query", {"table": "nope"})
    text = res.content[0].text  # type: ignore[union-attr]
    assert res.is_error and "[not_found]" in text and "sb-secret-xyz123" not in text


async def test_full_delete_flow_over_mcp() -> None:
    fake = FakePostgrest(sample_tables())
    server = build(
        fake,
        NEXUS_ALLOW_WRITES=True,
        NEXUS_ALLOW_DELETES=True,
        SUPABASE_WRITABLE_TABLES="notes",
    )
    args = {"table": "notes", "filters": [{"column": "id", "value": 3}]}
    async with Client(server) as c:
        dry = await c.call_tool("supabase_delete", args)  # default is a dry run
        assert dry.structured_content["dry_run"] is True and len(fake.tables["notes"]) == 3
        no_confirm = await c.call_tool("supabase_delete", {**args, "dry_run": False})
        assert no_confirm.is_error and "[confirmation_required]" in no_confirm.content[0].text  # type: ignore[union-attr]
        done = await c.call_tool("supabase_delete", {**args, "dry_run": False, "expected_count": 1})
    assert done.structured_content["affected_count"] == 1 and len(fake.tables["notes"]) == 2


async def test_write_to_non_writable_table_refused_over_mcp() -> None:
    server = build(
        FakePostgrest(sample_tables()), NEXUS_ALLOW_WRITES=True, SUPABASE_WRITABLE_TABLES="notes"
    )
    async with Client(server) as c:
        res = await c.call_tool(
            "supabase_insert", {"table": "secrets", "rows": [{"value": "x"}], "dry_run": False}
        )
    assert res.is_error and "[write_not_permitted]" in res.content[0].text  # type: ignore[union-attr]


async def test_server_info_lists_supabase_and_writable_tables() -> None:
    server = build(
        FakePostgrest(sample_tables()), NEXUS_ALLOW_WRITES=True, SUPABASE_WRITABLE_TABLES="notes"
    )
    async with Client(server) as c:
        data = (await c.call_tool("server_info", {})).structured_content
    assert data["sources"][0]["name"] == "supabase"
    assert data["config"]["supabase_writable_tables"] == ["notes"]
