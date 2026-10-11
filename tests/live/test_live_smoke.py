"""Opt-in smoke tests against the REAL Notion / Supabase APIs. Read-only.

Skipped unless NEXUS_LIVE_TESTS=1 and the relevant credentials are in the environment, so CI and
forks never touch real data. They exist to catch API drift that the fakes cannot (the fakes encode
what we believe the APIs do; these check that belief).

    NEXUS_LIVE_TESTS=1 NOTION_TOKEN=... SUPABASE_URL=... SUPABASE_KEY=... pytest -m live
"""

from __future__ import annotations

import os

import pytest
from mcp import Client

from nexus_mcp.core.config import Settings
from nexus_mcp.providers.registry import build_registry
from nexus_mcp.server import create_server

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("NEXUS_LIVE_TESTS") != "1", reason="set NEXUS_LIVE_TESTS=1"),
]


def _client(**overrides: object) -> Client:
    # Writes are forced off no matter what the environment says.
    settings = Settings(NEXUS_ALLOW_WRITES=False, NEXUS_ALLOW_DELETES=False, **overrides)  # type: ignore[arg-type]
    return Client(create_server(settings, build_registry(settings)))


@pytest.mark.skipif(not os.environ.get("NOTION_TOKEN"), reason="NOTION_TOKEN not set")
async def test_notion_search_and_health() -> None:
    async with _client() as client:
        health = await client.call_tool("health_check", {})
        assert not health.is_error
        sources = {s["source"]: s for s in health.structured_content["sources"]}
        assert sources["notion"]["ok"] is True, sources["notion"]
        result = await client.call_tool("notion_search", {"query": "a", "limit": 3})
        assert not result.is_error, result


@pytest.mark.skipif(
    not (os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_KEY")),
    reason="SUPABASE_URL/SUPABASE_KEY not set",
)
async def test_supabase_lists_tables_and_health() -> None:
    async with _client() as client:
        health = await client.call_tool("health_check", {})
        sources = {s["source"]: s for s in health.structured_content["sources"]}
        assert sources["supabase"]["ok"] is True, sources["supabase"]
        tables = await client.call_tool("supabase_list_tables", {})
        assert not tables.is_error, tables
