"""Structured inputs and outputs for Notion tools."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from nexus_mcp.core.sanitize import SafeDict, SafeStr


class _Out(BaseModel):
    model_config = ConfigDict(frozen=True)


class NotionObject(_Out):
    id: str
    type: Literal["page", "database"]
    title: SafeStr
    url: str | None = None
    last_edited_time: str | None = None
    archived: bool = False


class NotionSearchResult(_Out):
    items: list[NotionObject]
    has_more: bool
    next_cursor: str | None = None


class NotionPage(_Out):
    id: str
    title: SafeStr
    url: str | None = None
    parent: dict[str, str | None]
    last_edited_time: str
    archived: bool
    properties: SafeDict
    content: SafeStr | None = Field(None, description="Top-level blocks rendered as markdown")
    content_truncated: bool = False
    flags: list[str] = Field(default_factory=list, description="Heuristic injection warnings")
    skipped_blocks: int = Field(0, description="Blocks of unsupported types that were omitted")


class PropertyInfo(_Out):
    name: SafeStr
    type: str
    options: list[SafeStr] | None = Field(
        None, description="Allowed values for select/status types"
    )


class NotionDatabase(_Out):
    id: str
    title: SafeStr
    url: str | None = None
    properties: list[PropertyInfo]


class NotionRow(_Out):
    id: str
    title: SafeStr
    url: str | None = None
    last_edited_time: str | None = None
    properties: SafeDict


class NotionQueryResult(_Out):
    database_id: str
    rows: list[NotionRow]
    has_more: bool
    next_cursor: str | None = None


class NotionWriteResult(_Out):
    operation: Literal["create", "update", "append", "archive"]
    dry_run: bool = Field(description="True means NOTHING was changed")
    page_id: str | None = None
    url: str | None = None
    title: SafeStr | None = None
    last_edited_time: str | None = Field(
        None, description="Pass this as expected_last_edited_time when applying update/archive"
    )
    changes: SafeDict = Field(default_factory=dict)
    message: str


# ---- inputs ---------------------------------------------------------------------------------
class FilterOp(StrEnum):
    EQ = "eq"
    NEQ = "neq"
    CONTAINS = "contains"
    NOT_CONTAINS = "not_contains"
    STARTS_WITH = "starts_with"
    ENDS_WITH = "ends_with"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    BEFORE = "before"
    AFTER = "after"
    ON_OR_BEFORE = "on_or_before"
    ON_OR_AFTER = "on_or_after"
    IS_EMPTY = "is_empty"
    IS_NOT_EMPTY = "is_not_empty"


class NotionFilter(BaseModel):
    """One condition on a database property. The server looks up the property type for you."""

    model_config = ConfigDict(extra="forbid")

    property: str = Field(
        min_length=1, max_length=100, description="Property name, as in the schema"
    )
    op: FilterOp = Field(description="Operator; which ones apply depends on the property type")
    value: str | int | float | bool | None = Field(
        None, description="Comparison value (omit for is_empty / is_not_empty)"
    )


class NotionSort(BaseModel):
    model_config = ConfigDict(extra="forbid")

    by: str = Field(
        min_length=1,
        max_length=100,
        description="Property name, or created_time / last_edited_time",
    )
    direction: Literal["asc", "desc"] = "desc"
