"""Stateful Notion API emulator on httpx.MockTransport."""

from __future__ import annotations

import json
from typing import Any

import httpx

DB = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
PARENT_PAGE = "99999999-9999-9999-9999-999999999999"
P1 = "11111111-1111-1111-1111-111111111111"  # in DB
P2 = "22222222-2222-2222-2222-222222222222"  # under PARENT_PAGE
P3 = "33333333-3333-3333-3333-333333333333"  # in an unrelated DB
OTHER_DB = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
T0 = "2026-10-01T10:00:00.000Z"


def rt(text: str) -> list[dict[str, Any]]:
    return [{"type": "text", "plain_text": text, "text": {"content": text}}]


def schema() -> dict[str, Any]:
    def opts(*names: str) -> dict[str, Any]:
        return {"options": [{"name": n} for n in names]}

    return {
        "object": "database",
        "id": DB,
        "url": f"https://www.notion.so/{DB.replace('-', '')}",
        "title": rt("Tasks"),
        "properties": {
            "Name": {"type": "title", "title": {}},
            "Status": {"type": "status", "status": opts("Todo", "Done")},
            "Priority": {"type": "number", "number": {}},
            "Tags": {"type": "multi_select", "multi_select": opts("a", "b")},
            "Due": {"type": "date", "date": {}},
            "Done": {"type": "checkbox", "checkbox": {}},
            "Owner": {"type": "people", "people": {}},
            "Notes": {"type": "rich_text", "rich_text": {}},
            "Calc": {"type": "formula", "formula": {}},
        },
    }


def page(pid: str, title: str, parent: dict[str, str], status: str = "Todo") -> dict[str, Any]:
    return {
        "object": "page",
        "id": pid,
        "url": f"https://www.notion.so/{pid.replace('-', '')}",
        "archived": False,
        "last_edited_time": T0,
        "parent": parent,
        "properties": {
            "Name": {"type": "title", "title": rt(title)},
            "Status": {"type": "status", "status": {"name": status}},
            "Priority": {"type": "number", "number": 2},
            "Tags": {"type": "multi_select", "multi_select": [{"name": "a"}]},
        },
    }


def para(text: str) -> dict[str, Any]:
    return {"type": "paragraph", "paragraph": {"rich_text": rt(text)}}


class FakeNotion:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.bodies: list[Any] = []
        self.fail_queue: list[int] = []
        self.fail_patch: int | None = None
        self.pages = {
            P1: page(P1, "Ship MCP", {"type": "database_id", "database_id": DB}),
            P2: page(P2, "Notes page", {"type": "page_id", "page_id": PARENT_PAGE}),
            P3: page(P3, "Elsewhere", {"type": "database_id", "database_id": OTHER_DB}),
        }
        self.blocks: dict[str, list[dict[str, Any]]] = {
            P1: [
                {"type": "heading_1", "heading_1": {"rich_text": rt("Plan")}},
                para("Do the thing"),
                {"type": "to_do", "to_do": {"rich_text": rt("ship"), "checked": True}},
                {"type": "image", "image": {}},
            ]
        }
        self.tick = 0

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def last_body(self) -> Any:
        return self.bodies[-1]

    def _handle(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        body = json.loads(req.content) if req.content else None
        self.bodies.append(body)
        if self.fail_patch and req.method == "PATCH":
            return httpx.Response(self.fail_patch, json={"code": "boom", "message": "bad"})
        if self.fail_queue:
            return httpx.Response(
                self.fail_queue.pop(0),
                json={"code": "boom", "message": "bad"},
                headers={"retry-after": "0"},
            )
        path = req.url.path.removeprefix("/v1")
        m, q = req.method, dict(req.url.params)
        if path == "/users/me":
            return httpx.Response(200, json={"object": "user", "id": "u"})
        if path == "/search" and m == "POST":
            results: list[dict[str, Any]] = [*self.pages.values(), schema()]
            kind = (body.get("filter") or {}).get("value")
            if kind:
                results = [r for r in results if r["object"] == kind]
            text = body.get("query", "").lower()
            if text:
                results = [
                    r for r in results
                    if text in (r["title"][0]["plain_text"] if r["object"] == "database"
                                else r["properties"]["Name"]["title"][0]["plain_text"]).lower()
                ]  # fmt: skip
            return httpx.Response(
                200, json={"results": results, "has_more": False, "next_cursor": None}
            )
        if path == f"/databases/{DB}" and m == "GET":
            return httpx.Response(200, json=schema())
        if path == f"/databases/{DB}/query" and m == "POST":
            rows = [p for p in self.pages.values() if p["parent"].get("database_id") == DB]
            return httpx.Response(
                200, json={"results": rows, "has_more": True, "next_cursor": "c2"}
            )
        if path == "/pages" and m == "POST":
            new = page("44444444-4444-4444-4444-444444444444", "created", body["parent"])
            return httpx.Response(200, json=new)
        if path.startswith("/pages/"):
            pid = path.rsplit("/", 1)[1]
            if pid == DB:
                return httpx.Response(
                    400,
                    json={"code": "validation_error", "message": "ID is a database, not a page."},
                )
            if pid not in self.pages:
                return httpx.Response(404, json={"code": "object_not_found", "message": "nope"})
            if m == "GET":
                return httpx.Response(200, json=self.pages[pid])
            if m == "PATCH":
                self.tick += 1
                self.pages[pid]["last_edited_time"] = f"2026-10-02T10:00:0{self.tick}.000Z"
                if body.get("archived"):
                    self.pages[pid]["archived"] = True
                return httpx.Response(200, json=self.pages[pid])
        if path.startswith("/blocks/") and path.endswith("/children"):
            pid = path.split("/")[2]
            if m == "GET":
                items = self.blocks.get(pid, [])
                start = int(q.get("start_cursor", 0))
                size = int(q.get("page_size", 100))
                chunk = items[start : start + size]
                more = start + size < len(items)
                return httpx.Response(
                    200,
                    json={
                        "results": chunk,
                        "has_more": more,
                        "next_cursor": str(start + size) if more else None,
                    },
                )
            if m == "PATCH":
                return httpx.Response(200, json={"results": body["children"]})
        return httpx.Response(
            404, json={"code": "object_not_found", "message": f"unhandled {m} {path}"}
        )
