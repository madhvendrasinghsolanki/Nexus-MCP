"""Supabase data source built on the PostgREST API."""

from __future__ import annotations

import asyncio
import time
from typing import Any, ClassVar

import httpx

from nexus_mcp.core.config import Settings
from nexus_mcp.core.errors import NexusError, NotFound, ProviderError, ValidationFailed
from nexus_mcp.core.logging import get_logger
from nexus_mcp.core.models import Capability, KnowledgeDocument, KnowledgeItem, SourceKind
from nexus_mcp.core.sanitize import clip_json
from nexus_mcp.core.validation import check_payload, validate_identifier
from nexus_mcp.providers.base import DataSource
from nexus_mcp.providers.supabase.client import PostgrestClient
from nexus_mcp.providers.supabase.filters import (
    Filter,
    OrderBy,
    build_filter_params,
    check_column,
    escape_like_term,
    quote,
)
from nexus_mcp.providers.supabase.models import (
    ColumnInfo,
    QueryResult,
    TableInfo,
    WriteResult,
)
from nexus_mcp.services.write_guard import WritePolicy

log = get_logger(__name__)

_SPEC_TTL_S = 60.0
_PK_MARKER = "Primary Key"


class SupabaseSource(DataSource):
    name: ClassVar[str] = "supabase"
    kind: ClassVar[SourceKind] = SourceKind.SUPABASE
    description: ClassVar[str] = "Supabase Postgres tables via the REST API"

    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        assert settings.supabase_url and settings.supabase_key
        self._settings = settings
        self._allowed = settings.supabase_allowed_table_set
        self._policy = WritePolicy.for_supabase(settings)
        self._client = PostgrestClient(
            settings.supabase_url,
            settings.supabase_key.get_secret_value(),
            settings.supabase_schema,
            settings.request_timeout_s,
            transport,
        )
        self._spec: tuple[float, dict[str, Any]] | None = None

    # ---- DataSource contract -------------------------------------------------------------
    @property
    def capabilities(self) -> frozenset[Capability]:
        caps = {Capability.READ, Capability.SEARCH}
        if self._policy.can_write:
            caps.add(Capability.WRITE)
            if self._policy.allow_deletes:
                caps.add(Capability.DELETE)
        return frozenset(caps)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def health(self) -> str | None:
        if self._allowed:
            table = sorted(self._allowed)[0]
            await self._client.request("GET", f"/{table}", params=[("limit", "0")])
            return "reachable"
        try:
            await self._client.request("GET", "/", accept="application/openapi+json")
        except NotFound:
            return "reachable (schema introspection restricted)"
        return "reachable"

    async def search(self, query: str, limit: int) -> list[KnowledgeItem]:
        targets = {
            t: cols
            for t, cols in self._settings.supabase_search_map.items()
            if not self._allowed or t in self._allowed
        }
        if not targets:
            return []
        results = await asyncio.gather(
            *(self._search_table(t, cols, query, limit) for t, cols in targets.items()),
            return_exceptions=True,
        )
        items: list[KnowledgeItem] = []
        failures: list[BaseException] = []
        for table, res in zip(targets, results, strict=True):
            if isinstance(res, BaseException):
                log.warning("supabase_search_table_failed", table=table, error=type(res).__name__)
                failures.append(res)
                continue
            items.extend(res)
        if failures and len(failures) == len(targets):
            # Total failure must not look like "no results": let the caller mark the source failed.
            first = failures[0]
            raise (
                first if isinstance(first, NexusError) else ProviderError("Supabase search failed.")
            )
        return items[:limit]

    async def _search_table(
        self, table: str, cols: tuple[str, ...], query: str, limit: int
    ) -> list[KnowledgeItem]:
        term = escape_like_term(query.strip())
        if not term:
            raise ValidationFailed("Search query is empty after sanitising.")
        pattern = quote(f"*{term}*")
        expr = "(" + ",".join(f"{c}.ilike.{pattern}" for c in cols) + ")"
        resp = await self._client.request(
            "GET", f"/{table}", params=[("select", "*"), ("or", expr), ("limit", str(limit))]
        )
        out: list[KnowledgeItem] = []
        for i, row in enumerate(resp.json()):
            texts = [str(row[c]) for c in cols if row.get(c) not in (None, "")]
            out.append(
                KnowledgeItem(
                    source=self.name,
                    id=f"{table}:{row.get('id', i)}",
                    title=(texts[0] if texts else f"{table} row")[:120],
                    snippet=" ".join(texts[1:])[:300] or None,
                    metadata={"table": table},
                )
            )
        return out

    async def get(self, item_id: str) -> KnowledgeDocument:
        """`item_id` is `table:primary_key_value` (the id format produced by `search`)."""
        table, sep, key = item_id.partition(":")
        if not sep or not key:
            raise ValidationFailed(
                f"Invalid Supabase item id {item_id!r}.",
                hint="Expected 'table:id', e.g. 'notes:42'.",
            )
        result = await self.query(table, None, [Filter(column="id", value=key)], None, 1, 0)
        if not result.rows:
            raise NotFound(f"No row with id {key} in '{table}'.")
        row = result.rows[0]
        text_cols = self._settings.supabase_search_map.get(table, ())
        title = next((str(row[c]) for c in text_cols if row.get(c)), f"{table} #{key}")
        body = "\n".join(f"{k}: {v}" for k, v in row.items())
        return KnowledgeDocument(
            source=self.name, id=item_id, title=title[:200], content=body, fields=row,
            metadata={"table": table},
        )  # fmt: skip

    # ---- access control ------------------------------------------------------------------
    def _check_readable(self, table: str) -> str:
        validate_identifier(table, "table name")
        if self._allowed and table not in self._allowed:
            raise NotFound(
                f"Table '{table}' is not accessible.",
                hint="Use supabase_list_tables to see accessible tables.",
            )
        return table

    # ---- schema --------------------------------------------------------------------------
    async def _get_spec(self) -> dict[str, Any] | None:
        now = time.monotonic()
        if self._spec and now - self._spec[0] < _SPEC_TTL_S:
            return self._spec[1]
        try:
            resp = await self._client.request("GET", "/", accept="application/openapi+json")
            spec = resp.json()
        except (NotFound, ProviderError, ValueError):
            return None
        if not isinstance(spec, dict) or "definitions" not in spec:
            return None
        self._spec = (now, spec)
        return spec

    @staticmethod
    def _columns_from_definition(definition: dict[str, Any]) -> list[ColumnInfo]:
        required = set(definition.get("required", []))
        cols = []
        for cname, prop in definition.get("properties", {}).items():
            desc = str(prop.get("description", ""))
            is_pk = _PK_MARKER in desc
            first_line = desc.split("\n")[0].strip()
            cols.append(
                ColumnInfo(
                    name=cname,
                    type=prop.get("format") or prop.get("type"),
                    nullable=cname not in required,
                    is_primary_key=is_pk,
                    description=None if is_pk else (first_line or None),
                )
            )
        return cols

    async def list_tables(self) -> list[TableInfo]:
        spec = await self._get_spec()
        if spec is None:
            if self._allowed:
                return [TableInfo(name=t) for t in sorted(self._allowed)]
            raise ProviderError(
                "Schema introspection is not available for this key.",
                hint="Set SUPABASE_ALLOWED_TABLES, or use a key permitted to read the API schema.",
            )
        names = sorted(spec["definitions"])
        if self._allowed:
            names = [n for n in names if n in self._allowed]
        return [
            TableInfo(name=n, columns=self._columns_from_definition(spec["definitions"][n]))
            for n in names
        ]

    async def describe_table(self, table: str) -> TableInfo:
        self._check_readable(table)
        spec = await self._get_spec()
        if spec and table in spec["definitions"]:
            return TableInfo(
                name=table, columns=self._columns_from_definition(spec["definitions"][table])
            )
        # Fallback: infer from one row (also confirms the table exists).
        resp = await self._client.request("GET", f"/{table}", params=[("limit", "1")])
        rows = resp.json()
        if not rows:
            return TableInfo(name=table, columns=[], columns_inferred=True)
        cols = [
            ColumnInfo(name=k, type=type(v).__name__ if v is not None else None)
            for k, v in rows[0].items()
        ]
        return TableInfo(name=table, columns=cols, columns_inferred=True)

    # ---- reads ---------------------------------------------------------------------------
    async def query(
        self,
        table: str,
        columns: list[str] | None,
        filters: list[Filter] | None,
        order_by: list[OrderBy] | None,
        limit: int,
        offset: int,
    ) -> QueryResult:
        self._check_readable(table)
        limit = min(limit, self._settings.max_results)
        select = ",".join(check_column(c) for c in columns) if columns else "*"
        params = [("select", select), *build_filter_params(filters)]
        if order_by:
            params.append(("order", ",".join(f"{o.column}.{o.direction}" for o in order_by)))
        params += [("limit", str(limit + 1)), ("offset", str(offset))]
        rows = (await self._client.request("GET", f"/{table}", params=params)).json()
        page, clipped = clip_json(rows[:limit], self._settings.max_cell_chars)
        return QueryResult(
            table=table,
            rows=page,
            clipped_cells=clipped,
            returned=min(len(rows), limit),
            limit=limit,
            offset=offset,
            has_more=len(rows) > limit,
        )

    async def count(self, table: str, filters: list[Filter] | None) -> int:
        params = [("select", "*"), *build_filter_params(filters), ("limit", "0")]
        resp = await self._client.request("GET", f"/{table}", params=params, prefer="count=exact")
        total = resp.headers.get("content-range", "").rpartition("/")[2]
        if not total.isdigit():
            raise ProviderError("Could not determine the number of matching rows.")
        return int(total)

    # ---- writes (all gated by WritePolicy) -----------------------------------------------
    async def insert(self, table: str, rows: list[dict[str, Any]], dry_run: bool) -> WriteResult:
        validate_identifier(table, "table name")
        self._policy.check("insert", table)
        if not rows:
            raise ValidationFailed("`rows` must contain at least one row.")
        check_payload(rows, self._settings.max_payload_bytes, "rows payload")
        self._policy.check_row_budget(len(rows))
        for row in rows:
            if not row:
                raise ValidationFailed("Rows must not be empty.")
            for key in row:
                check_column(key)
        if dry_run:
            return WriteResult(
                table=table,
                operation="insert",
                dry_run=True,
                rows=rows[:5],
                message=f"Dry run: would insert {len(rows)} row(s). Nothing was changed. "
                "Repeat with dry_run=false to apply.",
            )
        resp = await self._client.request(
            "POST", f"/{table}", json=rows, prefer="return=representation"
        )
        created = resp.json()
        log.info("supabase_write", op="insert", table=table, rows=len(created))
        return WriteResult(
            table=table,
            operation="insert",
            dry_run=False,
            affected_count=len(created),
            rows=created,
            message=f"Inserted {len(created)} row(s).",
        )

    async def update(
        self,
        table: str,
        values: dict[str, Any],
        filters: list[Filter],
        dry_run: bool,
        expected_count: int | None,
    ) -> WriteResult:
        validate_identifier(table, "table name")
        self._policy.check("update", table)
        if not filters:
            raise ValidationFailed("At least one filter is required for updates.")
        if not values:
            raise ValidationFailed("`values` must not be empty.")
        check_payload(values, self._settings.max_payload_bytes, "values payload")
        for key in values:
            check_column(key)
        matched = await self.count(table, filters)
        self._policy.check_row_budget(matched)
        if dry_run:
            return WriteResult(
                table=table,
                operation="update",
                dry_run=True,
                matched_count=matched,
                message=f"Dry run: {matched} row(s) would be updated. Nothing was changed. "
                f"Repeat with dry_run=false and expected_count={matched} to apply.",
            )
        self._policy.check_confirmation(expected_count, matched)
        resp = await self._client.request(
            "PATCH",
            f"/{table}",
            params=build_filter_params(filters),
            json=values,
            prefer="return=representation",
        )
        updated = resp.json()
        log.info("supabase_write", op="update", table=table, rows=len(updated))
        return WriteResult(
            table=table,
            operation="update",
            dry_run=False,
            matched_count=matched,
            affected_count=len(updated),
            rows=updated,
            message=f"Updated {len(updated)} row(s).",
        )

    async def delete(
        self, table: str, filters: list[Filter], dry_run: bool, expected_count: int | None
    ) -> WriteResult:
        validate_identifier(table, "table name")
        self._policy.check("delete", table)
        if not filters:
            raise ValidationFailed("At least one filter is required for deletes.")
        matched = await self.count(table, filters)
        self._policy.check_row_budget(matched)
        if dry_run:
            return WriteResult(
                table=table,
                operation="delete",
                dry_run=True,
                matched_count=matched,
                message=f"Dry run: {matched} row(s) would be DELETED. Nothing was changed. "
                f"Repeat with dry_run=false and expected_count={matched} to apply.",
            )
        self._policy.check_confirmation(expected_count, matched)
        resp = await self._client.request(
            "DELETE",
            f"/{table}",
            params=build_filter_params(filters),
            prefer="return=representation",
        )
        deleted = resp.json()
        log.info("supabase_write", op="delete", table=table, rows=len(deleted))
        return WriteResult(
            table=table,
            operation="delete",
            dry_run=False,
            matched_count=matched,
            affected_count=len(deleted),
            rows=deleted,
            message=f"Deleted {len(deleted)} row(s).",
        )
