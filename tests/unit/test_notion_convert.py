from __future__ import annotations

from typing import Any

import pytest

from nexus_mcp.core.errors import ValidationFailed
from nexus_mcp.core.validation import parse_notion_id
from nexus_mcp.providers.notion import convert
from nexus_mcp.providers.notion.models import FilterOp, NotionFilter, NotionSort
from tests.support.notion_fake import DB, P1, page, schema

SCHEMA = schema()


def flt(prop: str, op: str, value: Any = None) -> NotionFilter:
    return NotionFilter(property=prop, op=FilterOp(op), value=value)


# ---- ids -------------------------------------------------------------------------------------
def test_parse_notion_id_forms() -> None:
    compact = P1.replace("-", "")
    assert parse_notion_id(compact) == P1
    assert parse_notion_id(P1.upper()) == P1
    assert parse_notion_id(f"https://www.notion.so/My-Page-Title-{compact}?pvs=4") == P1
    assert parse_notion_id(f"https://team.notion.site/{compact}") == P1


@pytest.mark.parametrize(
    "bad",
    ["", "nope", "1234", "http://notion.so/" + "a" * 32, "https://evil.com/" + "a" * 32,
     "https://notion.so.evil.com/" + "a" * 32, "../" + "a" * 32, "a" * 31, "g" * 32],
)  # fmt: skip
def test_parse_notion_id_rejects(bad: str) -> None:
    with pytest.raises(ValidationFailed):
        parse_notion_id(bad)


# ---- reading ---------------------------------------------------------------------------------
def test_flatten_properties_and_title() -> None:
    p = page(P1, "Hello", {"type": "workspace", "workspace": True})
    p["properties"]["Due"] = {"type": "date", "date": {"start": "2026-10-31", "end": None}}
    p["properties"]["Range"] = {"type": "date", "date": {"start": "a", "end": "b"}}
    p["properties"]["Calc"] = {"type": "formula", "formula": {"type": "number", "number": 7}}
    p["properties"]["Id"] = {"type": "unique_id", "unique_id": {"prefix": "T", "number": 4}}
    p["properties"]["Weird"] = {"type": "button", "button": {}}
    flat = convert.flatten_properties(p)
    assert convert.object_title(p) == "Hello"
    assert flat["Name"] == "Hello" and flat["Status"] == "Todo" and flat["Tags"] == ["a"]
    assert flat["Due"] == "2026-10-31" and flat["Range"] == {"start": "a", "end": "b"}
    assert flat["Calc"] == 7 and flat["Id"] == "T-4" and flat["Weird"] is None
    assert convert.object_title(SCHEMA) == "Tasks"


def test_database_properties_expose_options() -> None:
    props = {p.name: p for p in convert.database_properties(SCHEMA)}
    assert props["Status"].options == ["Todo", "Done"] and props["Priority"].options is None


def test_blocks_to_markdown() -> None:
    blocks = [
        {"type": "heading_2", "heading_2": {"rich_text": convert.rich_text("H")}},
        {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": convert.rich_text("b")}},
        {"type": "to_do", "to_do": {"rich_text": convert.rich_text("t"), "checked": False}},
        {"type": "code", "code": {"rich_text": convert.rich_text("x=1"), "language": "python"}},
        {"type": "divider", "divider": {}},
        {"type": "image", "image": {}},
    ]
    # rich_text() output lacks plain_text; mimic API responses
    for b in blocks:
        for v in b.values():
            if isinstance(v, dict) and "rich_text" in v:
                for t in v["rich_text"]:
                    t["plain_text"] = t["text"]["content"]
    md, skipped = convert.blocks_to_markdown(blocks)
    assert md == "## H\n\n- b\n\n- [ ] t\n\n```python\nx=1\n```\n\n---" and skipped == 1


# ---- text -> blocks --------------------------------------------------------------------------
def test_text_to_blocks_markdown_subset() -> None:
    text = "# Title\npara line 1\npara line 2\n\n- item\n- [x] done\n1. first\n> quote\n"
    kinds = [b["type"] for b in convert.text_to_blocks(text)]
    assert kinds == [
        "heading_1",
        "paragraph",
        "bulleted_list_item",
        "to_do",
        "numbered_list_item",
        "quote",
    ]
    para = convert.text_to_blocks("a\nb")[0]
    assert para["paragraph"]["rich_text"][0]["text"]["content"] == "a\nb"
    assert convert.text_to_blocks("- [x] z")[0]["to_do"]["checked"] is True


def test_long_text_is_chunked_and_block_limit_enforced() -> None:
    chunks = convert.text_to_blocks("x" * 4500)[0]["paragraph"]["rich_text"]
    assert [len(c["text"]["content"]) for c in chunks] == [2000, 2000, 500]
    with pytest.raises(ValidationFailed):
        convert.text_to_blocks("\n\n".join(["p"] * 101))


# ---- property values -------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("ptype", "value", "expected_key"),
    [("number", 3, "number"), ("select", "x", "select"), ("status", "Done", "status"),
     ("multi_select", ["a"], "multi_select"), ("checkbox", True, "checkbox"),
     ("date", "2026-10-31", "date"), ("date", {"start": "2026-10-31", "end": "2026-11-02"}, "date"),
     ("url", "https://x.co", "url"), ("title", "T", "title"), ("rich_text", "r", "rich_text")],
)  # fmt: skip
def test_build_property_value_ok(ptype: str, value: Any, expected_key: str) -> None:
    assert expected_key in convert.build_property_value("P", ptype, value)


@pytest.mark.parametrize(
    ("ptype", "value"),
    [("number", "3"), ("number", True), ("checkbox", "yes"), ("multi_select", "a"),
     ("date", "tomorrow"), ("date", {"end": "2026-01-01"}), ("title", 5), ("people", ["x"]),
     ("relation", ["x"]), ("formula", 1)],
)  # fmt: skip
def test_build_property_value_rejects(ptype: str, value: Any) -> None:
    with pytest.raises(ValidationFailed):
        convert.build_property_value("P", ptype, value)


def test_build_properties_unknown_name_lists_available() -> None:
    with pytest.raises(ValidationFailed) as e:
        convert.build_properties(SCHEMA, {"Nope": 1})
    assert "Status" in e.value.to_client_message()
    assert convert.title_property_name(SCHEMA) == "Name"


# ---- filters ---------------------------------------------------------------------------------
def test_filter_mapping_by_type() -> None:
    assert convert.build_filter(SCHEMA, flt("Status", "eq", "Done")) == {
        "property": "Status", "status": {"equals": "Done"}}  # fmt: skip
    assert convert.build_filter(SCHEMA, flt("Priority", "gte", 3))["number"] == {
        "greater_than_or_equal_to": 3}  # fmt: skip
    assert convert.build_filter(SCHEMA, flt("Name", "contains", "mcp"))["title"] == {
        "contains": "mcp"
    }
    assert convert.build_filter(SCHEMA, flt("Tags", "contains", "a"))["multi_select"] == {
        "contains": "a"
    }
    assert convert.build_filter(SCHEMA, flt("Due", "on_or_after", "2026-01-01"))["date"] == {
        "on_or_after": "2026-01-01"}  # fmt: skip
    assert convert.build_filter(SCHEMA, flt("Done", "eq", True))["checkbox"] == {"equals": True}
    assert convert.build_filter(SCHEMA, flt("Notes", "is_empty"))["rich_text"] == {"is_empty": True}


@pytest.mark.parametrize(
    "f",
    [flt("Missing", "eq", "x"), flt("Status", "gt", 1), flt("Priority", "eq", "high"),
     flt("Done", "eq", "true"), flt("Status", "eq"), flt("Name", "eq", 5), flt("Calc", "eq", 1)],
)  # fmt: skip
def test_filter_rejections(f: NotionFilter) -> None:
    with pytest.raises(ValidationFailed):
        convert.build_filter(SCHEMA, f)


def test_filter_error_lists_valid_operators() -> None:
    with pytest.raises(ValidationFailed) as e:
        convert.build_filter(SCHEMA, flt("Status", "contains", "x"))
    assert "eq" in e.value.to_client_message()


def test_query_body_and_sorts() -> None:
    body = convert.build_query_body(
        SCHEMA,
        [flt("Status", "eq", "Todo"), flt("Priority", "lt", 5)],
        [NotionSort(by="Priority", direction="asc"), NotionSort(by="last_edited_time")],
    )
    assert body["filter"]["and"][1]["number"] == {"less_than": 5}
    assert body["sorts"] == [
        {"property": "Priority", "direction": "ascending"},
        {"timestamp": "last_edited_time", "direction": "descending"},
    ]
    single = convert.build_query_body(SCHEMA, [flt("Status", "eq", "Todo")], None)
    assert "and" not in single["filter"]
    assert convert.build_query_body(SCHEMA, None, None) == {}
    with pytest.raises(ValidationFailed):
        convert.build_query_body(SCHEMA, None, [NotionSort(by="Nope")])
    _ = DB
