"""Golden snapshot of the advertised tool surface.

A tool's name, input schema, output schema and annotations are the server's public API: models
and prompts are written against them. Any change must be deliberate, so it must show up in review
as a diff of tests/snapshots/tool_surface.json.

Regenerate after an intentional change with `UPDATE_SNAPSHOTS=1 pytest <this file>`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from nexus_mcp.core.config import Settings
from nexus_mcp.providers.registry import build_registry
from nexus_mcp.server import create_server

pytestmark = pytest.mark.integration

SNAPSHOT = Path(__file__).parent.parent / "snapshots" / "tool_surface.json"
_ENV = dict(
    NEXUS_DEMO_MODE=True,
    SUPABASE_URL="https://example.supabase.co",
    SUPABASE_KEY="k" * 40,
    NOTION_TOKEN="t" * 40,
    SUPABASE_WRITABLE_TABLES="notes",
    NOTION_WRITABLE_PARENTS="11111111-1111-1111-1111-111111111111",
)
MODES: dict[str, dict[str, Any]] = {
    "read_only_default": {},
    "writes_enabled": {"NEXUS_ALLOW_WRITES": True},
    "writes_and_deletes_enabled": {"NEXUS_ALLOW_WRITES": True, "NEXUS_ALLOW_DELETES": True},
}


async def _surface(extra: dict[str, Any]) -> dict[str, Any]:
    settings = Settings(_env_file=None, **_ENV, **extra)  # type: ignore[call-arg, arg-type]
    server = create_server(settings, build_registry(settings))
    async with Client(server) as client:
        tools = (await client.list_tools()).tools
    return {
        t.name: {
            "read_only": bool(t.annotations and t.annotations.read_only_hint),
            "destructive": bool(t.annotations and t.annotations.destructive_hint),
            "input_schema": t.input_schema,
            "output_schema": t.output_schema,
        }
        for t in sorted(tools, key=lambda t: t.name)
    }


async def _current() -> dict[str, Any]:
    out = {mode: await _surface(extra) for mode, extra in MODES.items()}
    return json.loads(json.dumps(out, sort_keys=True))  # normalise


async def test_tool_surface_matches_snapshot() -> None:
    current = await _current()
    if os.environ.get("UPDATE_SNAPSHOTS") == "1":
        SNAPSHOT.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")
    assert SNAPSHOT.exists(), "Run with UPDATE_SNAPSHOTS=1 to create the snapshot"
    assert current == json.loads(SNAPSHOT.read_text()), (
        "Tool surface changed. If intentional, regenerate with UPDATE_SNAPSHOTS=1 and review."
    )


WRITE_TOOLS = {
    "supabase_insert", "supabase_update", "notion_create_page", "notion_update_page",
    "notion_append_content",
}  # fmt: skip
DELETE_TOOLS = {"supabase_delete", "notion_archive_page"}


async def test_safety_invariants_hold_in_every_mode() -> None:
    names = {mode: set(tools) for mode, tools in (await _current()).items()}
    off, writes, deletes = (
        names["read_only_default"],
        names["writes_enabled"],
        names["writes_and_deletes_enabled"],
    )
    assert not (off & (WRITE_TOOLS | DELETE_TOOLS)), "write tools advertised while writes are off"
    assert writes >= WRITE_TOOLS
    assert not (writes & DELETE_TOOLS), "delete tools must need NEXUS_ALLOW_DELETES"
    assert deletes >= DELETE_TOOLS


async def test_every_mutating_tool_defaults_to_dry_run() -> None:
    for mode, tools in (await _current()).items():
        for name, t in tools.items():
            if not t["read_only"]:
                default = t["input_schema"]["properties"].get("dry_run", {}).get("default")
                assert default is True, f"{mode}:{name} must default to dry_run=true"
