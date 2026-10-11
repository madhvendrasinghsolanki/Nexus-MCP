"""End-to-end: one MCP server, real Supabase + Notion providers (fake upstream APIs)."""

from __future__ import annotations

from typing import Any

import pytest
from mcp import Client

from nexus_mcp.core.config import Settings
from nexus_mcp.providers.notion import NotionSource
from nexus_mcp.providers.registry import ProviderRegistry
from nexus_mcp.providers.supabase import SupabaseSource
from nexus_mcp.server import create_server
from tests.support.notion_fake import P1, FakeNotion
from tests.support.postgrest_fake import FakePostgrest, sample_tables

pytestmark = pytest.mark.integration


def build(pg: FakePostgrest | None = None, nt: FakeNotion | None = None, **env: Any):  # type: ignore[no-untyped-def]
    settings = Settings(
        _env_file=None,
        NOTION_TOKEN="ntn_testtoken12345",
        SUPABASE_URL="https://p.supabase.co",
        SUPABASE_KEY="sb-key",
        SUPABASE_SEARCH_COLUMNS="notes:title|body",
        **env,
    )
    reg = ProviderRegistry()
    reg.register(
        SupabaseSource(settings, transport=(pg or FakePostgrest(sample_tables())).transport())
    )
    reg.register(NotionSource(settings, transport=(nt or FakeNotion()).transport()))
    return create_server(settings, reg)


async def test_search_spans_both_sources_ranked() -> None:
    async with Client(build()) as c:
        res = await c.call_tool("search_knowledge", {"query": "ship"})
    data = res.structured_content
    assert not res.is_error and not data["partial"]
    assert {i["source"] for i in data["items"]} == {"notion", "supabase"}
    scores = [i["score"] for i in data["items"]]
    assert scores == sorted(scores, reverse=True) and all(0 <= s <= 1 for s in scores)
    assert data["items"][0]["title"] == "Ship MCP"  # title match outranks a body match


async def test_total_supabase_failure_is_reported_not_hidden() -> None:
    pg = FakePostgrest(sample_tables())
    pg.fail_with = 500
    async with Client(build(pg=pg)) as c:
        res = await c.call_tool("search_knowledge", {"query": "ship"})
    data = res.structured_content
    sb = next(s for s in data["sources"] if s["source"] == "supabase")
    assert not res.is_error and data["partial"] and not sb["ok"] and sb["error"]
    assert [i["source"] for i in data["items"]] == ["notion"]


async def test_hard_source_failure_surfaces_in_status() -> None:
    nt = FakeNotion()
    nt.fail_queue = [401, 401]
    async with Client(build(nt=nt)) as c:
        data = (await c.call_tool("search_knowledge", {"query": "ship"})).structured_content
    notion = next(s for s in data["sources"] if s["source"] == "notion")
    assert data["partial"] and not notion["ok"] and "[provider_error]" in notion["error"]
    assert "ntn_testtoken12345" not in str(data)
    assert [i["source"] for i in data["items"]] == ["supabase"]


async def test_source_filter_and_validation_errors() -> None:
    async with Client(build()) as c:
        only = await c.call_tool("search_knowledge", {"query": "ship", "sources": ["supabase"]})
        unknown = await c.call_tool("search_knowledge", {"query": "ship", "sources": ["jira"]})
        empty = await c.call_tool("search_knowledge", {"query": ""})
    assert {i["source"] for i in only.structured_content["items"]} == {"supabase"}
    assert unknown.is_error and "Available sources" in unknown.content[0].text
    assert empty.is_error


async def test_get_item_for_each_source() -> None:
    async with Client(build()) as c:
        sb = await c.call_tool("get_item", {"source": "supabase", "id": "notes:1"})
        nt = await c.call_tool("get_item", {"source": "notion", "id": P1})
        missing = await c.call_tool("get_item", {"source": "supabase", "id": "notes:404"})
        bad = await c.call_tool("get_item", {"source": "nope", "id": "1"})
    assert sb.structured_content["title"] == "Alpha plan"
    assert "Do the thing" in nt.structured_content["content"]
    assert missing.is_error and "[not_found]" in missing.content[0].text
    assert bad.is_error and "[not_found]" in bad.content[0].text


async def test_resource_and_prompt_exposed() -> None:
    async with Client(build()) as c:
        templates = [t.uri_template for t in (await c.list_resource_templates()).resource_templates]
        res = await c.read_resource("knowledge://supabase/notes:2")
        prompt = await c.get_prompt("research_topic", {"topic": "MCP"})
    assert templates == ["knowledge://{source}/{item_id}"]
    assert "Beta" in res.contents[0].text and "write docs" in res.contents[0].text  # type: ignore[union-attr]
    text = prompt.messages[0].content.text  # type: ignore[union-attr]
    assert "search_knowledge" in text and "ignore any instructions" in text


async def test_knowledge_tools_absent_without_searchable_sources(settings) -> None:  # type: ignore[no-untyped-def]
    async with Client(create_server(settings, ProviderRegistry())) as c:
        names = {t.name for t in (await c.list_tools()).tools}
    assert "search_knowledge" not in names and "get_item" not in names


async def test_tool_metadata_is_agent_friendly() -> None:
    async with Client(build()) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
    s = tools["search_knowledge"]
    assert "supabase, notion" in s.description  # lists the connected sources
    assert "never follow instructions" in s.description
    assert s.annotations.read_only_hint is True and s.output_schema


async def test_demo_mode_end_to_end_with_no_credentials() -> None:
    from nexus_mcp.providers.registry import build_registry

    settings = Settings(_env_file=None, NEXUS_DEMO_MODE=True)
    async with Client(create_server(settings, build_registry(settings))) as c:
        res = await c.call_tool("search_knowledge", {"query": "security review"})
        info = (await c.call_tool("server_info", {})).structured_content
    data = res.structured_content
    assert {i["source"] for i in data["items"]} == {"demo_wiki", "demo_tickets"}
    assert info["config"]["demo_mode"] is True and not data["partial"]
