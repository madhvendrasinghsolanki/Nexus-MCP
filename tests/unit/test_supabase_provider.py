from __future__ import annotations

import httpx
import pytest

from nexus_mcp.core.config import Settings
from nexus_mcp.core.errors import (
    ConfirmationRequired,
    NotFound,
    ProviderError,
    UpstreamTimeout,
    ValidationFailed,
    WriteNotPermitted,
)
from nexus_mcp.providers.supabase import SupabaseSource, create_supabase_source
from nexus_mcp.providers.supabase.filters import Filter
from tests.support.postgrest_fake import FakePostgrest, sample_tables


def make_source(fake: FakePostgrest, **env: object) -> SupabaseSource:
    settings = Settings(
        _env_file=None,
        SUPABASE_URL="https://proj.supabase.co",
        SUPABASE_KEY="test-key-value",
        **env,  # type: ignore[arg-type]
    )
    return SupabaseSource(settings, transport=fake.transport())


WRITE = {
    "NEXUS_ALLOW_WRITES": True,
    "NEXUS_ALLOW_DELETES": True,
    "SUPABASE_WRITABLE_TABLES": "notes",
}
open_notes = [Filter(column="status", value="open")]


def test_factory_requires_credentials() -> None:
    assert create_supabase_source(Settings(_env_file=None)) is None


async def test_list_and_describe_use_openapi() -> None:
    src = make_source(FakePostgrest(sample_tables()))
    tables = {t.name: t for t in await src.list_tables()}
    assert set(tables) == {"notes", "secrets"}
    cols = {c.name: c for c in (await src.describe_table("notes")).columns}
    assert cols["id"].is_primary_key and cols["id"].nullable is False
    assert cols["title"].nullable is True


async def test_allowlist_hides_tables() -> None:
    src = make_source(FakePostgrest(sample_tables()), SUPABASE_ALLOWED_TABLES="notes")
    assert [t.name for t in await src.list_tables()] == ["notes"]
    with pytest.raises(NotFound):
        await src.describe_table("secrets")
    with pytest.raises(NotFound):
        await src.query("secrets", None, None, None, 5, 0)


async def test_describe_falls_back_to_sample_row_without_openapi() -> None:
    src = make_source(FakePostgrest(sample_tables(), openapi=False))
    info = await src.describe_table("notes")
    assert info.columns_inferred and {c.name for c in info.columns} >= {"id", "title"}
    with pytest.raises(ProviderError, match="introspection"):
        await src.list_tables()


async def test_query_filters_paging_and_cap() -> None:
    src = make_source(FakePostgrest(sample_tables()), NEXUS_MAX_RESULTS=1)
    r = await src.query("notes", ["id"], open_notes, None, limit=50, offset=0)
    assert r.limit == 1 and r.returned == 1 and r.has_more
    r2 = await src.query("notes", None, open_notes, None, limit=50, offset=1)
    assert [row["id"] for row in r2.rows] == [2] and not r2.has_more


async def test_query_rejects_bad_identifiers() -> None:
    src = make_source(FakePostgrest(sample_tables()))
    with pytest.raises(ValidationFailed):
        await src.query("notes; drop", None, None, None, 5, 0)
    with pytest.raises(ValidationFailed):
        await src.query("notes", ["id,secret"], None, None, 5, 0)


async def test_search_across_configured_tables() -> None:
    src = make_source(FakePostgrest(sample_tables()), SUPABASE_SEARCH_COLUMNS="notes:title|body")
    items = await src.search("mcp", 10)
    assert [i.id for i in items] == ["notes:1"]
    assert items[0].title == "Alpha plan" and items[0].snippet == "ship the MCP server"
    assert await make_source(FakePostgrest(sample_tables())).search("x", 5) == []


async def test_search_survives_a_failing_table() -> None:
    src = make_source(
        FakePostgrest(sample_tables()), SUPABASE_SEARCH_COLUMNS="notes:title,ghost:title"
    )
    assert [i.id for i in await src.search("beta", 10)] == ["notes:2"]


# ---- writes --------------------------------------------------------------------------------
async def test_writes_blocked_by_default() -> None:
    src = make_source(FakePostgrest(sample_tables()))
    with pytest.raises(WriteNotPermitted):
        await src.insert("notes", [{"title": "x"}], dry_run=False)
    assert Capability_names(src) == {"read", "search"}


def Capability_names(src: SupabaseSource) -> set[str]:
    return {c.value for c in src.capabilities}


async def test_insert_dry_run_then_apply() -> None:
    fake = FakePostgrest(sample_tables())
    src = make_source(fake, **WRITE)
    dry = await src.insert("notes", [{"id": 9, "title": "n"}], dry_run=True)
    assert dry.dry_run and len(fake.tables["notes"]) == 3
    done = await src.insert("notes", [{"id": 9, "title": "n"}], dry_run=False)
    assert done.affected_count == 1 and len(fake.tables["notes"]) == 4
    assert Capability_names(src) == {"read", "search", "write", "delete"}


async def test_insert_other_table_and_bad_keys_rejected() -> None:
    src = make_source(FakePostgrest(sample_tables()), **WRITE)
    with pytest.raises(WriteNotPermitted):
        await src.insert("secrets", [{"value": "x"}], dry_run=False)
    with pytest.raises(ValidationFailed):
        await src.insert("notes", [{"a b": 1}], dry_run=False)


async def test_update_requires_dry_run_confirmation() -> None:
    fake = FakePostgrest(sample_tables())
    src = make_source(fake, **WRITE)
    dry = await src.update("notes", {"status": "done"}, open_notes, True, None)
    assert dry.matched_count == 2 and dry.affected_count == 0
    assert all(r["status"] != "done" or r["id"] == 3 for r in fake.tables["notes"])
    with pytest.raises(ConfirmationRequired):
        await src.update("notes", {"status": "done"}, open_notes, False, None)
    with pytest.raises(ConfirmationRequired, match="Nothing was changed"):
        await src.update("notes", {"status": "done"}, open_notes, False, 1)
    res = await src.update("notes", {"status": "done"}, open_notes, False, 2)
    assert res.affected_count == 2 and {r["status"] for r in fake.tables["notes"]} == {"done"}


async def test_update_and_delete_need_filters() -> None:
    src = make_source(FakePostgrest(sample_tables()), **WRITE)
    with pytest.raises(ValidationFailed):
        await src.update("notes", {"status": "x"}, [], True, None)
    with pytest.raises(ValidationFailed):
        await src.delete("notes", [], True, None)


async def test_delete_flow_and_row_budget() -> None:
    fake = FakePostgrest(sample_tables())
    src = make_source(fake, **WRITE, NEXUS_MAX_WRITE_ROWS=1)
    with pytest.raises(WriteNotPermitted, match="2 rows"):
        await src.delete("notes", open_notes, True, None)
    one = [Filter(column="id", value=3)]
    assert (await src.delete("notes", one, True, None)).matched_count == 1
    res = await src.delete("notes", one, False, 1)
    assert res.affected_count == 1 and [r["id"] for r in fake.tables["notes"]] == [1, 2]


async def test_delete_needs_delete_flag() -> None:
    src = make_source(
        FakePostgrest(sample_tables()),
        NEXUS_ALLOW_WRITES=True,
        SUPABASE_WRITABLE_TABLES="notes",
    )
    with pytest.raises(WriteNotPermitted, match="Delete"):
        await src.delete("notes", open_notes, True, None)


# ---- errors & health -----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("status", "exc"),
    [
        (401, ProviderError),
        (403, ProviderError),
        (404, NotFound),
        (400, ValidationFailed),
        (500, ProviderError),
    ],
)
async def test_http_errors_map_to_safe_errors(status: int, exc: type[Exception]) -> None:
    fake = FakePostgrest(sample_tables())
    fake.fail_with = status
    src = make_source(fake)
    with pytest.raises(exc) as info:
        await src.query("notes", None, None, None, 5, 0)
    assert "test-key-value" not in str(info.value)


async def test_timeout_maps_to_upstream_timeout() -> None:
    def boom(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    settings = Settings(_env_file=None, SUPABASE_URL="https://p.supabase.co", SUPABASE_KEY="k")
    src = SupabaseSource(settings, transport=httpx.MockTransport(boom))
    with pytest.raises(UpstreamTimeout):
        await src.query("notes", None, None, None, 5, 0)


async def test_get_retries_once_on_503() -> None:
    fake = FakePostgrest(sample_tables())
    calls = {"n": 0}
    inner = fake.transport()

    def flaky(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503)
        return inner.handler(req)  # type: ignore[attr-defined,no-any-return]

    settings = Settings(_env_file=None, SUPABASE_URL="https://p.supabase.co", SUPABASE_KEY="k")
    src = SupabaseSource(settings, transport=httpx.MockTransport(flaky))
    assert (await src.query("notes", None, None, None, 5, 0)).returned == 3 and calls["n"] == 2


async def test_health_variants() -> None:
    assert await make_source(FakePostgrest(sample_tables())).health() == "reachable"
    restricted = make_source(FakePostgrest(sample_tables(), openapi=False))
    assert "restricted" in (await restricted.health() or "")
    fake = FakePostgrest(sample_tables())
    fake.fail_with = 401
    with pytest.raises(ProviderError):
        await make_source(fake, SUPABASE_ALLOWED_TABLES="notes").health()


async def test_requests_carry_auth_and_schema_headers() -> None:
    fake = FakePostgrest(sample_tables())
    src = make_source(fake, SUPABASE_SCHEMA="public")
    await src.query("notes", None, None, None, 1, 0)
    req = fake.requests[-1]
    assert req.headers["apikey"] == "test-key-value" and req.headers["accept-profile"] == "public"


async def test_get_item_by_table_and_id() -> None:
    src = make_source(FakePostgrest(sample_tables()), SUPABASE_SEARCH_COLUMNS="notes:title|body")
    doc = await src.get("notes:2")
    assert (
        doc.title == "Beta" and doc.fields["body"] == "write docs" and "status: open" in doc.content
    )
    for bad in ("notes", "notes:", ":1", "no tes:1"):
        with pytest.raises(ValidationFailed):
            await src.get(bad)
    with pytest.raises(NotFound):
        await src.get("notes:999")
    with pytest.raises(NotFound):
        await make_source(FakePostgrest(sample_tables()), SUPABASE_ALLOWED_TABLES="notes").get(
            "secrets:1"
        )


async def test_search_raises_when_every_table_fails() -> None:
    fake = FakePostgrest(sample_tables())
    fake.fail_with = 500
    src = make_source(fake, SUPABASE_SEARCH_COLUMNS="notes:title,docs:title")
    with pytest.raises(ProviderError):
        await src.search("x", 5)
