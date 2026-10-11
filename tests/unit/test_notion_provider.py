from __future__ import annotations

from typing import Any

import pytest

from nexus_mcp.core.config import Settings
from nexus_mcp.core.errors import (
    ConfirmationRequired,
    NotFound,
    ProviderError,
    ValidationFailed,
    WriteNotPermitted,
)
from nexus_mcp.providers.notion import NotionSource, create_notion_source
from nexus_mcp.providers.notion.models import FilterOp, NotionFilter, NotionSort
from tests.support.notion_fake import DB, P1, P2, P3, PARENT_PAGE, FakeNotion


def make(fake: FakeNotion, **env: Any) -> NotionSource:
    s = Settings(_env_file=None, NOTION_TOKEN="ntn_testtoken12345", **env)
    return NotionSource(s, transport=fake.transport())


RW = {"NEXUS_ALLOW_WRITES": True, "NOTION_WRITABLE_PARENTS": f"{DB},{PARENT_PAGE}"}
RWD = {**RW, "NEXUS_ALLOW_DELETES": True}


def test_factory_requires_token() -> None:
    assert create_notion_source(Settings(_env_file=None)) is None


async def test_search_filters_and_normalises() -> None:
    src = make(FakeNotion())
    res = await src.search_objects("ship", None, 10, None)
    assert [o.title for o in res.items] == ["Ship MCP"]
    dbs = await src.search_objects("", "database", 10, None)
    assert [o.type for o in dbs.items] == ["database"]
    items = await src.search("notes", 5)
    assert items[0].source == "notion" and items[0].metadata["object"] == "page"


async def test_empty_search_sorts_by_recent() -> None:
    fake = FakeNotion()
    await make(fake).search_objects("  ", "page", 10, "cur")
    body = fake.last_body()
    assert body["sort"]["timestamp"] == "last_edited_time" and body["start_cursor"] == "cur"
    assert "query" not in body and body["filter"] == {"property": "object", "value": "page"}


async def test_get_page_with_content() -> None:
    page = await make(FakeNotion()).get_page(P1, True, 100)
    assert page.title == "Ship MCP" and page.parent == {"type": "database_id", "id": DB}
    assert page.properties["Status"] == "Todo"
    assert page.content == "# Plan\n\nDo the thing\n\n- [x] ship" and page.skipped_blocks == 1
    assert not page.content_truncated
    assert (await make(FakeNotion()).get_page(P1, False, 1)).content is None


async def test_get_page_truncates_at_max_blocks() -> None:
    page = await make(FakeNotion()).get_page(P1, True, 2)
    assert page.content_truncated and page.content == "# Plan\n\nDo the thing"


async def test_get_page_accepts_url_and_reports_not_found() -> None:
    src = make(FakeNotion())
    assert (await src.get_page(f"https://notion.so/Ship-{P1.replace('-', '')}", False, 1)).id == P1
    with pytest.raises(NotFound):
        await src.get_page("55555555-5555-5555-5555-555555555555", False, 1)
    with pytest.raises(ValidationFailed):
        await src.get_page("../etc/passwd", False, 1)


async def test_get_database() -> None:
    db = await make(FakeNotion()).get_database(DB)
    assert db.title == "Tasks" and {p.name for p in db.properties} >= {"Name", "Status"}


async def test_query_database_sends_typed_filter() -> None:
    fake = FakeNotion()
    res = await make(fake, NEXUS_MAX_RESULTS=7).query_database(
        DB,
        [NotionFilter(property="Status", op=FilterOp.EQ, value="Todo")],
        [NotionSort(by="Priority", direction="asc")],
        50,
        "c1",
    )
    body = fake.last_body()
    assert body["filter"] == {"property": "Status", "status": {"equals": "Todo"}}
    assert body["page_size"] == 7 and body["start_cursor"] == "c1"
    assert res.has_more and res.next_cursor == "c2" and res.rows[0].properties["Priority"] == 2


# ---- writes ----------------------------------------------------------------------------------
async def test_writes_blocked_by_default() -> None:
    src = make(FakeNotion())
    with pytest.raises(WriteNotPermitted):
        await src.create_page(DB, "database", "x", None, None, False)
    with pytest.raises(WriteNotPermitted):
        await src.update_page(P1, {"Name": "x"}, False, "v")
    assert {c.value for c in src.capabilities} == {"read", "search"}


async def test_create_in_database_dry_run_then_apply() -> None:
    fake = FakeNotion()
    src = make(fake, **RW)
    props = {"Priority": 3, "Tags": ["a"]}
    dry = await src.create_page(DB, "database", "New task", props, "# Hi\n- a", True)
    assert dry.dry_run and dry.changes["blocks"] == 2 and fake.requests[-1].method == "GET"
    done = await src.create_page(DB, "database", "New task", props, "# Hi\n- a", False)
    body = fake.last_body()
    assert done.page_id and body["parent"] == {"database_id": DB}
    assert body["properties"]["Name"]["title"][0]["text"]["content"] == "New task"
    assert body["properties"]["Priority"] == {"number": 3} and len(body["children"]) == 2


async def test_create_under_page_and_validation() -> None:
    fake = FakeNotion()
    src = make(fake, **RW)
    await src.create_page(PARENT_PAGE, "page", "Child", None, None, False)
    assert fake.last_body()["parent"] == {"page_id": PARENT_PAGE}
    with pytest.raises(ValidationFailed):
        await src.create_page(PARENT_PAGE, "page", "Child", {"x": 1}, None, True)
    with pytest.raises(ValidationFailed):
        await src.create_page(DB, "database", None, {"Priority": 1}, None, True)
    with pytest.raises(ValidationFailed):
        await src.create_page(DB, "database", "t", {"Owner": ["me"]}, None, True)


async def test_create_outside_writable_parent_refused() -> None:
    src = make(FakeNotion(), **RW)
    with pytest.raises(WriteNotPermitted, match="not writable"):
        await src.create_page(
            "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "database", "x", None, None, False
        )


async def test_update_flow_with_version_check() -> None:
    fake = FakeNotion()
    src = make(fake, **RW)
    dry = await src.update_page(P1, {"Status": "Done", "Priority": 9}, True, None)
    assert dry.changes["Status"] == {"from": "Todo", "to": "Done"} and dry.last_edited_time
    patches = [r for r in fake.requests if r.method == "PATCH"]
    assert not patches
    with pytest.raises(ConfirmationRequired):
        await src.update_page(P1, {"Status": "Done"}, False, None)
    with pytest.raises(ConfirmationRequired, match="changed since"):
        await src.update_page(P1, {"Status": "Done"}, False, "2020-01-01T00:00:00.000Z")
    res = await src.update_page(P1, {"Status": "Done"}, False, dry.last_edited_time)
    assert fake.last_body()["properties"] == {"Status": {"status": {"name": "Done"}}}
    assert res.operation == "update"
    # stale after the page changed
    with pytest.raises(ConfirmationRequired):
        await src.update_page(P1, {"Status": "Todo"}, False, dry.last_edited_time)


async def test_update_page_under_page_only_title_and_scope_enforced() -> None:
    fake = FakeNotion()
    src = make(fake, **RW)
    dry = await src.update_page(P2, {"title": "Renamed"}, True, None)
    assert dry.changes["title"]["to"] == "Renamed"
    with pytest.raises(ValidationFailed):
        await src.update_page(P2, {"Status": "x"}, True, None)
    with pytest.raises(WriteNotPermitted):
        await src.update_page(P3, {"Name": "x"}, True, None)


async def test_page_itself_can_be_whitelisted() -> None:
    src = make(FakeNotion(), NEXUS_ALLOW_WRITES=True, NOTION_WRITABLE_PARENTS=P1)
    assert (await src.update_page(P1, {"Name": "ok"}, True, None)).dry_run


async def test_append_content() -> None:
    fake = FakeNotion()
    src = make(fake, **RW)
    dry = await src.append_content(P1, "one\n\ntwo", True)
    assert dry.changes == {"blocks_to_append": 2}
    assert not [r for r in fake.requests if r.method == "PATCH"]
    await src.append_content(P1, "one\n\ntwo", False)
    assert len(fake.last_body()["children"]) == 2
    with pytest.raises(ValidationFailed):
        await src.append_content(P1, "\n\n", True)


async def test_archive_requires_delete_flag_and_confirmation() -> None:
    with pytest.raises(WriteNotPermitted, match="Delete"):
        await make(FakeNotion(), **RW).archive_page(P1, True, None)
    fake = FakeNotion()
    src = make(fake, **RWD)
    dry = await src.archive_page(P1, True, None)
    assert "restorable" in dry.message and not fake.pages[P1]["archived"]
    with pytest.raises(ConfirmationRequired):
        await src.archive_page(P1, False, None)
    await src.archive_page(P1, False, dry.last_edited_time)
    assert fake.pages[P1]["archived"] and fake.last_body() == {"archived": True}
    with pytest.raises(ValidationFailed, match="archived"):
        await src.archive_page(P1, True, None)
    assert {c.value for c in src.capabilities} == {"read", "search", "write", "delete"}


# ---- errors ----------------------------------------------------------------------------------
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
async def test_http_errors_map_without_leaking(status: int, exc: type[Exception]) -> None:
    fake = FakeNotion()
    fake.fail_queue = [status] * 3
    with pytest.raises(exc) as info:
        await make(fake).get_page(P1, False, 1)
    assert "ntn_testtoken12345" not in str(info.value)


async def test_rate_limit_retried_once_for_reads() -> None:
    fake = FakeNotion()
    fake.fail_queue = [429]
    assert (await make(fake).get_page(P1, False, 1)).id == P1
    fake.fail_queue = [429, 429]
    with pytest.raises(ProviderError, match="rate limit"):
        await make(fake).get_page(P1, False, 1)


async def test_writes_are_not_retried() -> None:
    fake = FakeNotion()
    src = make(fake, **RW)
    fake.fail_queue = []
    dry = await src.update_page(P1, {"Status": "Done"}, True, None)
    fake.fail_patch = 503
    with pytest.raises(ProviderError):
        await src.update_page(P1, {"Status": "Done"}, False, dry.last_edited_time)
    assert len([r for r in fake.requests if r.method == "PATCH"]) == 1


async def test_health_and_auth_header() -> None:
    fake = FakeNotion()
    assert await make(fake).health() == "reachable"
    req = fake.requests[-1]
    assert req.headers["authorization"] == "Bearer ntn_testtoken12345"
    assert req.headers["notion-version"] == "2022-06-28"


async def test_get_item_page_and_database() -> None:
    src = make(FakeNotion())
    doc = await src.get(P1)
    assert (
        doc.title == "Ship MCP" and "Status: Todo" in doc.content and "Do the thing" in doc.content
    )
    assert doc.metadata["object"] == "page" and doc.fields["Priority"] == 2
    db = await src.get(DB)
    assert db.metadata["object"] == "database" and "Status (status): Todo, Done" in db.content
