"""End-to-end over MCP: Notion tools, policy-driven visibility, and a two-source server."""

from __future__ import annotations

from typing import Any

import pytest
from mcp import Client

from nexus_mcp.core.config import Settings
from nexus_mcp.providers.notion import NotionSource
from nexus_mcp.providers.registry import ProviderRegistry
from nexus_mcp.providers.supabase import SupabaseSource
from nexus_mcp.server import create_server
from tests.support.notion_fake import DB, P1, FakeNotion
from tests.support.postgrest_fake import FakePostgrest, sample_tables

pytestmark = pytest.mark.integration


def build(fake: FakeNotion, **env: Any):  # type: ignore[no-untyped-def]
    settings = Settings(_env_file=None, NOTION_TOKEN="ntn_testtoken12345", **env)
    reg = ProviderRegistry()
    reg.register(NotionSource(settings, transport=fake.transport()))
    return create_server(settings, reg)


async def tools(server) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    async with Client(server) as c:
        return {t.name: t for t in (await c.list_tools()).tools}


async def test_visibility_follows_policy() -> None:
    ro = await tools(build(FakeNotion()))
    assert {
        "notion_search",
        "notion_get_page",
        "notion_get_database",
        "notion_query_database",
    } <= set(ro)
    assert not {"notion_create_page", "notion_update_page", "notion_archive_page"} & set(ro)
    rw = await tools(build(FakeNotion(), NEXUS_ALLOW_WRITES=True))
    assert {"notion_create_page", "notion_update_page", "notion_append_content"} <= set(rw)
    assert "notion_archive_page" not in rw
    full = await tools(build(FakeNotion(), NEXUS_ALLOW_WRITES=True, NEXUS_ALLOW_DELETES=True))
    assert full["notion_archive_page"].annotations.destructive_hint is True


async def test_read_tools_warn_about_untrusted_content() -> None:
    t = await tools(build(FakeNotion()))
    for name in ("notion_search", "notion_get_page", "notion_query_database"):
        assert "untrusted" in t[name].description
        assert t[name].annotations.read_only_hint is True


async def test_search_get_and_query_over_mcp() -> None:
    server = build(FakeNotion())
    async with Client(server) as c:
        s = await c.call_tool("notion_search", {"query": "ship"})
        p = await c.call_tool("notion_get_page", {"page_id": P1})
        q = await c.call_tool(
            "notion_query_database",
            {"database_id": DB, "filters": [{"property": "Status", "op": "eq", "value": "Todo"}]},
        )
    assert s.structured_content["items"][0]["title"] == "Ship MCP"
    assert "Do the thing" in p.structured_content["content"]
    assert q.structured_content["rows"][0]["properties"]["Status"] == "Todo"


async def test_bad_filter_and_missing_page_errors() -> None:
    async with Client(build(FakeNotion())) as c:
        bad = await c.call_tool(
            "notion_query_database",
            {"database_id": DB, "filters": [{"property": "Status", "op": "gt", "value": 1}]},
        )
        missing = await c.call_tool("notion_get_page", {"page_id": "5" * 32})
        junk = await c.call_tool("notion_get_page", {"page_id": "not-an-id"})
    assert bad.is_error and "[validation_error]" in bad.content[0].text
    assert missing.is_error and "[not_found]" in missing.content[0].text
    assert junk.is_error and "[validation_error]" in junk.content[0].text


async def test_update_flow_over_mcp() -> None:
    fake = FakeNotion()
    server = build(fake, NEXUS_ALLOW_WRITES=True, NOTION_WRITABLE_PARENTS=DB)
    async with Client(server) as c:
        dry = await c.call_tool(
            "notion_update_page", {"page_id": P1, "properties": {"Status": "Done"}}
        )
        assert dry.structured_content["dry_run"] is True
        blind = await c.call_tool(
            "notion_update_page",
            {"page_id": P1, "properties": {"Status": "Done"}, "dry_run": False},
        )
        assert blind.is_error and "[confirmation_required]" in blind.content[0].text
        ok = await c.call_tool(
            "notion_update_page",
            {
                "page_id": P1,
                "properties": {"Status": "Done"},
                "dry_run": False,
                "expected_last_edited_time": dry.structured_content["last_edited_time"],
            },
        )
    assert ok.structured_content["dry_run"] is False


async def test_write_outside_policy_refused_over_mcp() -> None:
    server = build(FakeNotion(), NEXUS_ALLOW_WRITES=True, NOTION_WRITABLE_PARENTS="b" * 32)
    async with Client(server) as c:
        res = await c.call_tool(
            "notion_create_page",
            {"parent_id": DB, "parent_type": "database", "title": "x", "dry_run": False},
        )
    assert res.is_error and "[write_not_permitted]" in res.content[0].text


async def test_two_sources_side_by_side() -> None:
    settings = Settings(
        _env_file=None,
        NOTION_TOKEN="ntn_testtoken12345",
        SUPABASE_URL="https://p.supabase.co",
        SUPABASE_KEY="sb-key",
    )
    reg = ProviderRegistry()
    reg.register(SupabaseSource(settings, transport=FakePostgrest(sample_tables()).transport()))
    reg.register(NotionSource(settings, transport=FakeNotion().transport()))
    async with Client(create_server(settings, reg)) as c:
        info = (await c.call_tool("server_info", {})).structured_content
        health = (await c.call_tool("health_check", {})).structured_content
    assert {s["name"] for s in info["sources"]} == {"supabase", "notion"}
    assert health["ok"] is True and len(health["sources"]) == 2
