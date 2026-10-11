"""Pure conversion logic: Notion JSON <-> flat, LLM-friendly values. No I/O here."""

from __future__ import annotations

import re
from typing import Any

from nexus_mcp.core.errors import ValidationFailed
from nexus_mcp.providers.notion.models import FilterOp, NotionFilter, NotionSort, PropertyInfo

RICH_TEXT_LIMIT = 2000
MAX_CREATE_BLOCKS = 100
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}([T ][\d:.+\-Zz]+)?$")


# ---- reading ----
def plain(rich: list[dict[str, Any]] | None) -> str:
    return "".join(t.get("plain_text", "") for t in rich or [])


def object_title(obj: dict[str, Any]) -> str:
    if obj.get("object") == "database":
        return plain(obj.get("title")) or "Untitled"
    for prop in obj.get("properties", {}).values():
        if prop.get("type") == "title":
            return plain(prop.get("title")) or "Untitled"
    return "Untitled"


def flatten_property(prop: dict[str, Any]) -> Any:
    t = prop.get("type")
    v = prop.get(t) if t else None
    if t in ("title", "rich_text"):
        return plain(v)
    if t in ("number", "checkbox", "url", "email", "phone_number", "created_time",
             "last_edited_time"):  # fmt: skip
        return v
    if t in ("select", "status"):
        return v.get("name") if v else None
    if t == "multi_select":
        return [o.get("name") for o in v or []]
    if t == "date":
        if not v:
            return None
        return v["start"] if not v.get("end") else {"start": v["start"], "end": v["end"]}
    if t == "people":
        return [u.get("name") or u.get("id") for u in v or []]
    if t == "relation":
        return [r.get("id") for r in v or []]
    if t == "files":
        return [f.get("name") for f in v or []]
    if t == "formula":
        return flatten_property(v) if isinstance(v, dict) and "type" in v else None
    if t == "rollup":
        if not v:
            return None
        rt = v.get("type")
        if rt == "array":
            return [flatten_property(x) for x in v.get("array", [])]
        return v.get(rt) if rt != "date" else flatten_property({"type": "date", "date": v["date"]})
    if t in ("created_by", "last_edited_by"):
        return (v or {}).get("name") or (v or {}).get("id")
    if t == "unique_id":
        return (
            f"{v.get('prefix') or ''}{'-' if v.get('prefix') else ''}{v.get('number')}"
            if v
            else None
        )
    return None


def flatten_properties(obj: dict[str, Any]) -> dict[str, Any]:
    return {name: flatten_property(p) for name, p in obj.get("properties", {}).items()}


def database_properties(db: dict[str, Any]) -> list[PropertyInfo]:
    out = []
    for name, prop in db.get("properties", {}).items():
        t = prop["type"]
        opts = None
        if t in ("select", "status", "multi_select"):
            opts = [o["name"] for o in prop.get(t, {}).get("options", [])]
        out.append(PropertyInfo(name=name, type=t, options=opts))
    return out


_RICH_BLOCKS = {
    "paragraph": "",
    "heading_1": "# ",
    "heading_2": "## ",
    "heading_3": "### ",
    "bulleted_list_item": "- ",
    "numbered_list_item": "1. ",
    "quote": "> ",
    "callout": "> ",
    "toggle": "",
}


def blocks_to_markdown(blocks: list[dict[str, Any]]) -> tuple[str, int]:
    lines: list[str] = []
    skipped = 0
    for b in blocks:
        t = b.get("type")
        data = b.get(t, {}) if t else {}
        if t in _RICH_BLOCKS:
            lines.append(_RICH_BLOCKS[t] + plain(data.get("rich_text")))
        elif t == "to_do":
            lines.append(
                f"- [{'x' if data.get('checked') else ' '}] {plain(data.get('rich_text'))}"
            )
        elif t == "code":
            lines.append(f"```{data.get('language', '')}\n{plain(data.get('rich_text'))}\n```")
        elif t == "divider":
            lines.append("---")
        else:
            skipped += 1
    return "\n\n".join(lines), skipped


# ---- writing: text -> blocks ----
def rich_text(text: str) -> list[dict[str, Any]]:
    chunks = [text[i : i + RICH_TEXT_LIMIT] for i in range(0, len(text), RICH_TEXT_LIMIT)] or [""]
    return [{"type": "text", "text": {"content": c}} for c in chunks]


def _block(kind: str, text: str, **extra: Any) -> dict[str, Any]:
    return {"object": "block", "type": kind, kind: {"rich_text": rich_text(text), **extra}}


def text_to_blocks(content: str) -> list[dict[str, Any]]:
    """Small markdown subset: #/##/### headings, - bullets, 1. numbers, > quotes, - [ ] to-dos."""
    blocks: list[dict[str, Any]] = []
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            blocks.append(_block("paragraph", "\n".join(paragraph)))
            paragraph.clear()

    for raw in content.replace("\r\n", "\n").split("\n"):
        line = raw.rstrip()
        if not line.strip():
            flush()
        elif m := re.match(r"^(#{1,3})\s+(.*)$", line):
            flush()
            blocks.append(_block(f"heading_{len(m.group(1))}", m.group(2)))
        elif m := re.match(r"^[-*]\s+\[([ xX])\]\s+(.*)$", line):
            flush()
            blocks.append(_block("to_do", m.group(2), checked=m.group(1).lower() == "x"))
        elif m := re.match(r"^[-*]\s+(.*)$", line):
            flush()
            blocks.append(_block("bulleted_list_item", m.group(1)))
        elif m := re.match(r"^\d+[.)]\s+(.*)$", line):
            flush()
            blocks.append(_block("numbered_list_item", m.group(1)))
        elif m := re.match(r"^>\s?(.*)$", line):
            flush()
            blocks.append(_block("quote", m.group(1)))
        else:
            paragraph.append(line)
    flush()
    if len(blocks) > MAX_CREATE_BLOCKS:
        raise ValidationFailed(
            f"Content has {len(blocks)} blocks; the limit per call is {MAX_CREATE_BLOCKS}."
        )
    return blocks


# ---- writing: properties ----
def build_property_value(name: str, ptype: str, value: Any) -> dict[str, Any]:
    def bad(msg: str) -> ValidationFailed:
        return ValidationFailed(f"Property '{name}' ({ptype}): {msg}")

    if ptype in ("title", "rich_text"):
        if not isinstance(value, str):
            raise bad("expected a string")
        return {ptype: rich_text(value)}
    if ptype == "number":
        if isinstance(value, bool) or not isinstance(value, int | float | type(None)):
            raise bad("expected a number")
        return {"number": value}
    if ptype in ("select", "status"):
        if value is not None and not isinstance(value, str):
            raise bad("expected an option name")
        return {ptype: None if value is None else {"name": value}}
    if ptype == "multi_select":
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            raise bad("expected a list of option names")
        return {"multi_select": [{"name": x} for x in value]}
    if ptype == "checkbox":
        if not isinstance(value, bool):
            raise bad("expected true or false")
        return {"checkbox": value}
    if ptype == "date":
        if value is None:
            return {"date": None}
        start, end = (
            (value, None)
            if isinstance(value, str)
            else (
                (value.get("start"), value.get("end")) if isinstance(value, dict) else (None, None)
            )
        )
        for d in (start, end):
            if d is not None and not (isinstance(d, str) and _ISO_DATE.match(d)):
                raise bad("expected an ISO date like 2026-10-31 or {start, end}")
        if start is None:
            raise bad("expected an ISO date like 2026-10-31 or {start, end}")
        return {"date": {"start": start, "end": end}}
    if ptype in ("url", "email", "phone_number"):
        if value is not None and not isinstance(value, str):
            raise bad("expected a string")
        return {ptype: value}
    raise ValidationFailed(
        f"Property '{name}' has type '{ptype}', which this server cannot write.",
        hint="Writable types: title, rich_text, number, select, status, multi_select, checkbox, "
        "date, url, email, phone_number.",
    )


def build_properties(schema: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    props_schema: dict[str, Any] = schema.get("properties", {})
    out: dict[str, Any] = {}
    for name, value in values.items():
        if name not in props_schema:
            avail = ", ".join(sorted(props_schema)[:20])
            raise ValidationFailed(f"Unknown property '{name}'.", hint=f"Available: {avail}")
        out[name] = build_property_value(name, props_schema[name]["type"], value)
    return out


def title_property_name(schema: dict[str, Any]) -> str:
    for name, prop in schema.get("properties", {}).items():
        if prop.get("type") == "title":
            return str(name)
    raise ValidationFailed("Database has no title property.")


# ---- querying: simplified filter -> Notion filter ----
_TEXT = {
    FilterOp.EQ: "equals", FilterOp.NEQ: "does_not_equal", FilterOp.CONTAINS: "contains",
    FilterOp.NOT_CONTAINS: "does_not_contain", FilterOp.STARTS_WITH: "starts_with",
    FilterOp.ENDS_WITH: "ends_with", FilterOp.IS_EMPTY: "is_empty",
    FilterOp.IS_NOT_EMPTY: "is_not_empty",
}  # fmt: skip
_NUMBER = {
    FilterOp.EQ: "equals", FilterOp.NEQ: "does_not_equal", FilterOp.GT: "greater_than",
    FilterOp.GTE: "greater_than_or_equal_to", FilterOp.LT: "less_than",
    FilterOp.LTE: "less_than_or_equal_to", FilterOp.IS_EMPTY: "is_empty",
    FilterOp.IS_NOT_EMPTY: "is_not_empty",
}  # fmt: skip
_CHOICE = {
    FilterOp.EQ: "equals", FilterOp.NEQ: "does_not_equal", FilterOp.IS_EMPTY: "is_empty",
    FilterOp.IS_NOT_EMPTY: "is_not_empty",
}  # fmt: skip
_MULTI = {
    FilterOp.CONTAINS: "contains", FilterOp.NOT_CONTAINS: "does_not_contain",
    FilterOp.IS_EMPTY: "is_empty", FilterOp.IS_NOT_EMPTY: "is_not_empty",
}  # fmt: skip
_DATE = {
    FilterOp.EQ: "equals", FilterOp.BEFORE: "before", FilterOp.AFTER: "after",
    FilterOp.ON_OR_BEFORE: "on_or_before", FilterOp.ON_OR_AFTER: "on_or_after",
    FilterOp.IS_EMPTY: "is_empty", FilterOp.IS_NOT_EMPTY: "is_not_empty",
}  # fmt: skip
_CHECKBOX = {FilterOp.EQ: "equals", FilterOp.NEQ: "does_not_equal"}

_OPS_BY_TYPE: dict[str, dict[FilterOp, str]] = {
    **dict.fromkeys(("title", "rich_text", "url", "email", "phone_number"), _TEXT),
    "number": _NUMBER,
    "select": _CHOICE,
    "status": _CHOICE,
    "multi_select": _MULTI,
    "people": _MULTI,
    "relation": _MULTI,
    "date": _DATE,
    "created_time": _DATE,
    "last_edited_time": _DATE,
    "checkbox": _CHECKBOX,
}
_EMPTY_OPS = {FilterOp.IS_EMPTY, FilterOp.IS_NOT_EMPTY}


def build_filter(schema: dict[str, Any], f: NotionFilter) -> dict[str, Any]:
    prop = schema.get("properties", {}).get(f.property)
    if prop is None:
        avail = ", ".join(sorted(schema.get("properties", {}))[:20])
        raise ValidationFailed(f"Unknown property '{f.property}'.", hint=f"Available: {avail}")
    ptype = prop["type"]
    table = _OPS_BY_TYPE.get(ptype)
    if table is None:
        raise ValidationFailed(f"Filtering on '{ptype}' properties is not supported.")
    if f.op not in table:
        ok = ", ".join(sorted(o.value for o in table))
        raise ValidationFailed(
            f"Operator '{f.op.value}' is not valid for {ptype}.", hint=f"Use: {ok}"
        )
    key = table[f.op]
    if f.op in _EMPTY_OPS:
        cond: Any = True
    else:
        cond = f.value
        if cond is None:
            raise ValidationFailed(f"Operator '{f.op.value}' needs a value.")
        if ptype == "number" and (isinstance(cond, bool) or not isinstance(cond, int | float)):
            raise ValidationFailed(f"Property '{f.property}' needs a numeric value.")
        if ptype == "checkbox" and not isinstance(cond, bool):
            raise ValidationFailed(f"Property '{f.property}' needs true or false.")
        if ptype not in ("number", "checkbox") and not isinstance(cond, str):
            raise ValidationFailed(f"Property '{f.property}' needs a string value.")
    if ptype in ("created_time", "last_edited_time"):
        return {"timestamp": ptype, ptype: {key: cond}}
    return {"property": f.property, ptype: {key: cond}}


def build_query_body(
    schema: dict[str, Any], filters: list[NotionFilter] | None, sorts: list[NotionSort] | None
) -> dict[str, Any]:
    body: dict[str, Any] = {}
    built = [build_filter(schema, f) for f in filters or []]
    if len(built) == 1:
        body["filter"] = built[0]
    elif built:
        body["filter"] = {"and": built}
    if sorts:
        out = []
        for s in sorts:
            direction = "ascending" if s.direction == "asc" else "descending"
            if s.by in ("created_time", "last_edited_time"):
                out.append({"timestamp": s.by, "direction": direction})
            elif s.by in schema.get("properties", {}):
                out.append({"property": s.by, "direction": direction})
            else:
                raise ValidationFailed(f"Unknown sort property '{s.by}'.")
        body["sorts"] = out
    return body
