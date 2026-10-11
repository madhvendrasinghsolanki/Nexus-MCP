"""Supabase tools. Read tools always register; write tools register only when enabled."""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations
from pydantic import Field

from nexus_mcp.core.errors import ProviderError
from nexus_mcp.providers.supabase import SupabaseSource
from nexus_mcp.providers.supabase.filters import Filter, OrderBy
from nexus_mcp.providers.supabase.models import QueryResult, TableInfo, WriteResult
from nexus_mcp.tools._common import READ_ONLY, App, Audit, guarded

Table = Annotated[str, Field(description="Table name (use supabase_list_tables to discover)")]
Filters = Annotated[
    list[Filter], Field(description="Conditions combined with AND (required)", min_length=1)
]
DryRun = Annotated[
    bool,
    Field(description="If true (default) nothing is changed; the result previews the effect"),
]
ExpectedCount = Annotated[
    int | None,
    Field(ge=0, description="Required when dry_run=false: the matched_count from a dry run"),
]

_INSERT = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True
)
_UPDATE = ToolAnnotations(
    read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=True
)
_DELETE = ToolAnnotations(
    read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=True
)


def _source(app: App) -> SupabaseSource:
    src = app.registry.get("supabase")
    if not isinstance(src, SupabaseSource):  # pragma: no cover - registry invariant
        raise ProviderError("Supabase source is misconfigured.")
    return src


def register(mcp: MCPServer, app: App) -> None:
    if "supabase" not in app.registry:
        return  # not configured -> no Supabase tools are advertised
    src = _source(app)

    @mcp.tool(
        name="supabase_list_tables",
        title="List Supabase tables",
        description="List accessible Supabase tables with their columns. Start here.",
        annotations=READ_ONLY,
    )
    @guarded(app)
    async def supabase_list_tables() -> list[TableInfo]:
        return await src.list_tables()

    @mcp.tool(
        name="supabase_describe_table",
        title="Describe a Supabase table",
        description="Column names, types, nullability and primary key for one table.",
        annotations=READ_ONLY,
    )
    @guarded(app)
    async def supabase_describe_table(table: Table) -> TableInfo:
        return await src.describe_table(table)

    @mcp.tool(
        name="supabase_query",
        title="Query a Supabase table",
        description=(
            "Read rows with structured filters, ordering and pagination. Results are capped by "
            "server policy; check `has_more` and use `offset` to page."
        ),
        annotations=READ_ONLY,
    )
    @guarded(app)
    async def supabase_query(
        table: Table,
        columns: Annotated[
            list[str] | None, Field(description="Columns to return (default: all)")
        ] = None,
        filters: Annotated[
            list[Filter] | None, Field(description="Conditions combined with AND")
        ] = None,
        order_by: Annotated[list[OrderBy] | None, Field(description="Sort order")] = None,
        limit: Annotated[
            int, Field(ge=1, le=200, description="Max rows (server may cap lower)")
        ] = 10,
        offset: Annotated[int, Field(ge=0, description="Rows to skip")] = 0,
    ) -> QueryResult:
        return await src.query(table, columns, filters, order_by, limit, offset)

    if not app.settings.allow_writes:
        return

    @mcp.tool(
        name="supabase_insert",
        title="Insert Supabase rows",
        description=(
            "Insert rows into a writable table. Defaults to a dry run that changes nothing; "
            "set dry_run=false to apply."
        ),
        annotations=_INSERT,
    )
    @guarded(app, Audit("supabase", "insert", "table"))
    async def supabase_insert(
        table: Table,
        rows: Annotated[
            list[dict[str, Any]], Field(description="Rows as column->value objects", min_length=1)
        ],
        dry_run: DryRun = True,
    ) -> WriteResult:
        return await src.insert(table, rows, dry_run)

    @mcp.tool(
        name="supabase_update",
        title="Update Supabase rows",
        description=(
            "Update rows matching filters. Two-step: first call with dry_run=true to see "
            "matched_count, then dry_run=false with expected_count equal to it."
        ),
        annotations=_UPDATE,
    )
    @guarded(app, Audit("supabase", "update", "table"))
    async def supabase_update(
        table: Table,
        values: Annotated[dict[str, Any], Field(description="Columns to set", min_length=1)],
        filters: Filters,
        dry_run: DryRun = True,
        expected_count: ExpectedCount = None,
    ) -> WriteResult:
        return await src.update(table, values, filters, dry_run, expected_count)

    if not app.settings.allow_deletes:
        return

    @mcp.tool(
        name="supabase_delete",
        title="Delete Supabase rows",
        description=(
            "PERMANENTLY delete rows matching filters. Two-step: dry_run=true first, then "
            "dry_run=false with expected_count equal to matched_count. Always confirm with the "
            "user before the second step."
        ),
        annotations=_DELETE,
    )
    @guarded(app, Audit("supabase", "delete", "table"))
    async def supabase_delete(
        table: Table,
        filters: Filters,
        dry_run: DryRun = True,
        expected_count: ExpectedCount = None,
    ) -> WriteResult:
        return await src.delete(table, filters, dry_run, expected_count)
