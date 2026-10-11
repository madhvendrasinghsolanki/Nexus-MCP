"""Notion data source."""

from __future__ import annotations

from typing import Any, ClassVar

import httpx

from nexus_mcp.core.config import Settings
from nexus_mcp.core.errors import ValidationFailed
from nexus_mcp.core.logging import get_logger
from nexus_mcp.core.models import Capability, KnowledgeDocument, KnowledgeItem, SourceKind
from nexus_mcp.core.sanitize import clip_json, scan_for_injection
from nexus_mcp.core.validation import check_payload, parse_notion_id
from nexus_mcp.providers.base import DataSource
from nexus_mcp.providers.notion import convert
from nexus_mcp.providers.notion.client import NotionClient
from nexus_mcp.providers.notion.models import (
    NotionDatabase,
    NotionFilter,
    NotionObject,
    NotionPage,
    NotionQueryResult,
    NotionRow,
    NotionSearchResult,
    NotionSort,
    NotionWriteResult,
)
from nexus_mcp.services.write_guard import WritePolicy

log = get_logger(__name__)

_BLOCK_PAGE_SIZE = 100


def _summary(obj: dict[str, Any]) -> NotionObject:
    return NotionObject(
        id=obj["id"],
        type=obj["object"],
        title=convert.object_title(obj),
        url=obj.get("url"),
        last_edited_time=obj.get("last_edited_time"),
        archived=bool(obj.get("archived") or obj.get("in_trash")),
    )


class NotionSource(DataSource):
    name: ClassVar[str] = "notion"
    kind: ClassVar[SourceKind] = SourceKind.NOTION
    description: ClassVar[str] = "Notion pages and databases shared with the integration"

    def __init__(
        self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        assert settings.notion_token
        self._settings = settings
        self._policy = WritePolicy.for_notion(settings)
        self._client = NotionClient(
            settings.notion_token.get_secret_value(),
            settings.notion_api_version,
            settings.request_timeout_s,
            transport,
        )

    # ---- DataSource contract ------------------------------------------------------------
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
        await self._client.request("GET", "/users/me")
        return "reachable"

    async def search(self, query: str, limit: int) -> list[KnowledgeItem]:
        res = await self.search_objects(query, None, limit, None)
        return [
            KnowledgeItem(
                source=self.name,
                id=o.id,
                title=o.title,
                url=o.url,
                metadata={"object": o.type, "last_edited_time": o.last_edited_time},
            )
            for o in res.items
            if not o.archived
        ]

    async def get(self, item_id: str) -> KnowledgeDocument:
        """Fetch a page (or, if the id is a database, its schema) as a document."""
        try:
            page = await self.get_page(item_id, True, 200)
        except ValidationFailed:
            try:
                db = await self.get_database(item_id)
            except Exception:
                raise
            lines = [f"{p.name} ({p.type})" + (f": {', '.join(p.options)}" if p.options else "")
                     for p in db.properties]  # fmt: skip
            return KnowledgeDocument(
                source=self.name, id=db.id, title=db.title, url=db.url,
                content="Database properties:\n" + "\n".join(lines),
                fields={p.name: p.type for p in db.properties}, metadata={"object": "database"},
            )  # fmt: skip
        props = "\n".join(
            f"{k}: {v}" for k, v in page.properties.items() if v not in (None, "", [])
        )
        content = "\n\n".join(x for x in (props, page.content) if x)
        return KnowledgeDocument(
            source=self.name, id=page.id, title=page.title, url=page.url, content=content,
            fields=page.properties,
            metadata={"object": "page", "last_edited_time": page.last_edited_time,
                      "content_truncated": page.content_truncated, "archived": page.archived},
        )  # fmt: skip

    # ---- reads --------------------------------------------------------------------------
    async def search_objects(
        self, query: str, object_type: str | None, limit: int, cursor: str | None
    ) -> NotionSearchResult:
        body: dict[str, Any] = {"page_size": min(limit, self._settings.max_results, 100)}
        if query.strip():
            body["query"] = query.strip()
        else:
            body["sort"] = {"direction": "descending", "timestamp": "last_edited_time"}
        if object_type:
            body["filter"] = {"property": "object", "value": object_type}
        if cursor:
            body["start_cursor"] = cursor
        data = await self._client.request("POST", "/search", json=body, idempotent=True)
        return NotionSearchResult(
            items=[_summary(o) for o in data.get("results", [])],
            has_more=bool(data.get("has_more")),
            next_cursor=data.get("next_cursor"),
        )

    async def get_database_schema(self, database_id: str) -> dict[str, Any]:
        return await self._client.request("GET", f"/databases/{parse_notion_id(database_id)}")

    async def get_database(self, database_id: str) -> NotionDatabase:
        db = await self.get_database_schema(database_id)
        return NotionDatabase(
            id=db["id"],
            title=convert.object_title(db),
            url=db.get("url"),
            properties=convert.database_properties(db),
        )

    async def _get_page_raw(self, page_id: str) -> dict[str, Any]:
        return await self._client.request("GET", f"/pages/{parse_notion_id(page_id)}")

    @staticmethod
    def _parent(page: dict[str, Any]) -> dict[str, str | None]:
        parent = page.get("parent", {})
        ptype = parent.get("type", "unknown")
        return {"type": ptype, "id": parent.get(ptype) if ptype != "workspace" else None}

    async def get_page(self, page_id: str, include_content: bool, max_blocks: int) -> NotionPage:
        page = await self._get_page_raw(page_id)
        content: str | None = None
        truncated, skipped = False, 0
        if include_content:
            blocks: list[dict[str, Any]] = []
            cursor: str | None = None
            more = True
            while more and len(blocks) < max_blocks:
                size = min(_BLOCK_PAGE_SIZE, max_blocks - len(blocks))
                params: dict[str, str | int] = {"page_size": size}
                if cursor:
                    params["start_cursor"] = cursor
                data = await self._client.request(
                    "GET", f"/blocks/{page['id']}/children", params=params
                )
                blocks.extend(data.get("results", []))
                cursor = data.get("next_cursor")
                more = bool(data.get("has_more"))
            truncated = more
            content, skipped = convert.blocks_to_markdown(blocks)
            limit = self._settings.max_payload_bytes
            if len(content) > limit:
                content, truncated = content[:limit] + "\n[content truncated]", True
        props, _ = clip_json(convert.flatten_properties(page), self._settings.max_cell_chars)
        return NotionPage(
            id=page["id"],
            title=convert.object_title(page),
            url=page.get("url"),
            parent=self._parent(page),
            last_edited_time=page["last_edited_time"],
            archived=bool(page.get("archived") or page.get("in_trash")),
            properties=props,
            flags=scan_for_injection(f"{convert.object_title(page)}\n{content or ''}"),
            content=content,
            content_truncated=truncated,
            skipped_blocks=skipped,
        )

    async def query_database(
        self,
        database_id: str,
        filters: list[NotionFilter] | None,
        sorts: list[NotionSort] | None,
        limit: int,
        cursor: str | None,
    ) -> NotionQueryResult:
        db_id = parse_notion_id(database_id)
        schema = await self.get_database_schema(db_id)
        body = convert.build_query_body(schema, filters, sorts)
        body["page_size"] = min(limit, self._settings.max_results, 100)
        if cursor:
            body["start_cursor"] = cursor
        data = await self._client.request(
            "POST", f"/databases/{db_id}/query", json=body, idempotent=True
        )
        rows = [
            NotionRow(
                id=p["id"],
                title=convert.object_title(p),
                url=p.get("url"),
                last_edited_time=p.get("last_edited_time"),
                properties=convert.flatten_properties(p),
            )
            for p in data.get("results", [])
        ]
        return NotionQueryResult(
            database_id=db_id,
            rows=rows,
            has_more=bool(data.get("has_more")),
            next_cursor=data.get("next_cursor"),
        )

    # ---- writes (all gated by WritePolicy) ----------------------------------------------
    def _scope_ids(self, page: dict[str, Any]) -> list[str]:
        parent = self._parent(page)
        ids = [page["id"]]
        if parent["id"]:
            ids.insert(0, parent["id"])
        return [parse_notion_id(i) for i in ids]

    async def _writable_page(self, op: str, page_id: str) -> dict[str, Any]:
        page = await self._get_page_raw(page_id)
        self._policy.check(op, self._scope_ids(page))  # type: ignore[arg-type]
        if page.get("archived") or page.get("in_trash"):
            raise ValidationFailed("That page is archived.")
        return page

    async def create_page(
        self,
        parent_id: str,
        parent_type: str,
        title: str | None,
        properties: dict[str, Any] | None,
        content: str | None,
        dry_run: bool,
    ) -> NotionWriteResult:
        pid = parse_notion_id(parent_id, "parent ID")
        self._policy.check("insert", pid)
        blocks = convert.text_to_blocks(content) if content else []
        values = dict(properties or {})
        check_payload(values, self._settings.max_payload_bytes, "properties payload")
        if parent_type == "database":
            schema = await self.get_database_schema(pid)
            if title is not None:
                values[convert.title_property_name(schema)] = title
            props = convert.build_properties(schema, values)
            page_title = title or next(
                (v for k, v in values.items() if schema["properties"][k]["type"] == "title"), None
            )
            parent: dict[str, str] = {"database_id": pid}
        else:
            if properties:
                raise ValidationFailed("Pages under a page only take a title and content.")
            props = convert.build_property_value("title", "title", title or "")
            props = {"title": props}
            page_title, parent = title, {"page_id": pid}
        if not page_title:
            raise ValidationFailed("A title is required.")
        preview = {"parent": parent, "properties": values, "blocks": len(blocks)}
        if dry_run:
            return NotionWriteResult(
                operation="create", dry_run=True, title=page_title, changes=preview,
                message="Dry run: would create this page. Nothing was changed. "
                "Repeat with dry_run=false to apply.",
            )  # fmt: skip
        body: dict[str, Any] = {"parent": parent, "properties": props}
        if blocks:
            body["children"] = blocks
        page = await self._client.request("POST", "/pages", json=body)
        log.info("notion_write", op="create", parent=pid)
        return NotionWriteResult(
            operation="create", dry_run=False, page_id=page["id"], url=page.get("url"),
            title=page_title, last_edited_time=page.get("last_edited_time"),
            message="Created page.",
        )  # fmt: skip

    async def update_page(
        self,
        page_id: str,
        properties: dict[str, Any],
        dry_run: bool,
        expected_last_edited_time: str | None,
    ) -> NotionWriteResult:
        if not properties:
            raise ValidationFailed("`properties` must not be empty.")
        check_payload(properties, self._settings.max_payload_bytes, "properties payload")
        page = await self._writable_page("update", page_id)
        parent = self._parent(page)
        if parent["type"] == "database_id" and parent["id"]:
            schema = await self.get_database_schema(parent["id"])
        else:
            schema = {"properties": {"title": {"type": "title"}}}
        new_props = convert.build_properties(schema, properties)
        current = convert.flatten_properties(page)
        changes = {k: {"from": current.get(k), "to": v} for k, v in properties.items()}
        version = page["last_edited_time"]
        if dry_run:
            return NotionWriteResult(
                operation="update", dry_run=True, page_id=page["id"], url=page.get("url"),
                title=convert.object_title(page), last_edited_time=version, changes=changes,
                message="Dry run: nothing was changed. Repeat with dry_run=false and "
                f"expected_last_edited_time={version} to apply.",
            )  # fmt: skip
        self._policy.check_version(expected_last_edited_time, version)
        updated = await self._client.request(
            "PATCH", f"/pages/{page['id']}", json={"properties": new_props}
        )
        log.info("notion_write", op="update")
        return NotionWriteResult(
            operation="update", dry_run=False, page_id=updated["id"], url=updated.get("url"),
            title=convert.object_title(updated), last_edited_time=updated.get("last_edited_time"),
            changes=changes, message="Updated page properties.",
        )  # fmt: skip

    async def append_content(self, page_id: str, content: str, dry_run: bool) -> NotionWriteResult:
        blocks = convert.text_to_blocks(content)
        if not blocks:
            raise ValidationFailed("`content` must not be empty.")
        page = await self._writable_page("insert", page_id)
        title = convert.object_title(page)
        if dry_run:
            return NotionWriteResult(
                operation="append", dry_run=True, page_id=page["id"], url=page.get("url"),
                title=title, changes={"blocks_to_append": len(blocks)},
                message=f"Dry run: would append {len(blocks)} block(s). Nothing was changed. "
                "Repeat with dry_run=false to apply.",
            )  # fmt: skip
        await self._client.request(
            "PATCH", f"/blocks/{page['id']}/children", json={"children": blocks}
        )
        log.info("notion_write", op="append", blocks=len(blocks))
        return NotionWriteResult(
            operation="append", dry_run=False, page_id=page["id"], url=page.get("url"),
            title=title, changes={"blocks_appended": len(blocks)},
            message=f"Appended {len(blocks)} block(s).",
        )  # fmt: skip

    async def archive_page(
        self, page_id: str, dry_run: bool, expected_last_edited_time: str | None
    ) -> NotionWriteResult:
        page = await self._writable_page("delete", page_id)
        version = page["last_edited_time"]
        title = convert.object_title(page)
        if dry_run:
            return NotionWriteResult(
                operation="archive", dry_run=True, page_id=page["id"], url=page.get("url"),
                title=title, last_edited_time=version,
                message=f"Dry run: '{title}' would be moved to the Notion trash (restorable). "
                f"Repeat with dry_run=false and expected_last_edited_time={version} to apply.",
            )  # fmt: skip
        self._policy.check_version(expected_last_edited_time, version)
        await self._client.request("PATCH", f"/pages/{page['id']}", json={"archived": True})
        log.info("notion_write", op="archive")
        return NotionWriteResult(
            operation="archive", dry_run=False, page_id=page["id"], url=page.get("url"),
            title=title, message="Page moved to the Notion trash (restorable from Notion).",
        )  # fmt: skip
