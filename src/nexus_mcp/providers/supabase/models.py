"""Structured outputs for Supabase tools."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from nexus_mcp.core.sanitize import SafeRows, SafeStr


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True)


class ColumnInfo(_Model):
    name: SafeStr
    type: str | None = None
    nullable: bool | None = None
    is_primary_key: bool = False
    description: SafeStr | None = None


class TableInfo(_Model):
    name: SafeStr
    columns: list[ColumnInfo] = Field(default_factory=list)
    columns_inferred: bool = Field(
        False, description="True when columns were inferred from a sample row, not the schema"
    )


class QueryResult(_Model):
    table: str
    rows: SafeRows
    clipped_cells: int = Field(0, description="Over-long values that were truncated")
    returned: int
    limit: int = Field(description="Row limit actually applied (capped by server policy)")
    offset: int
    has_more: bool


class WriteResult(_Model):
    table: str
    operation: Literal["insert", "update", "delete"]
    dry_run: bool = Field(description="True means NOTHING was changed")
    matched_count: int | None = Field(None, description="Rows matching the filters before writing")
    affected_count: int = Field(0, description="Rows actually written (0 for dry runs)")
    rows: SafeRows = Field(default_factory=list)
    message: str
