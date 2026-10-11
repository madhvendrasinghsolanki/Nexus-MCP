"""A tiny stateful PostgREST emulator on httpx.MockTransport (eq/neq/in/ilike-or, count, CRUD)."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx


class FakePostgrest:
    def __init__(self, tables: dict[str, list[dict[str, Any]]], openapi: bool = True) -> None:
        self.tables = tables
        self.openapi = openapi
        self.requests: list[httpx.Request] = []
        self.fail_with: int | None = None

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    # -- helpers ------------------------------------------------------------------------
    def _spec(self) -> dict[str, Any]:
        defs = {
            t: {
                "required": ["id"],
                "properties": {
                    k: {
                        "type": "integer" if isinstance(v, int) else "string",
                        "description": "Note:\nThis is a Primary Key.<pk/>" if k == "id" else k,
                    }
                    for k, v in (rows[0] if rows else {"id": 1}).items()
                },
            }
            for t, rows in self.tables.items()
        }
        return {"swagger": "2.0", "definitions": defs}

    @staticmethod
    def _match(row: dict[str, Any], col: str, expr: str) -> bool:
        op, _, val = expr.partition(".")
        cell = row.get(col)
        if op == "eq":
            return str(cell) == val
        if op == "neq":
            return str(cell) != val
        if op == "in":
            items = [x.strip('"') for x in val.strip("()").split(",")]
            return str(cell) in items
        if op == "is":
            return (cell is None) == (val == "null")
        raise AssertionError(f"fake does not support op {op}")

    def _filter(
        self, rows: list[dict[str, Any]], params: list[tuple[str, str]]
    ) -> list[dict[str, Any]]:
        control = {"select", "limit", "offset", "order", "or"}
        out = rows
        for key, expr in params:
            if key in control:
                continue
            out = [r for r in out if self._match(r, key, expr)]
        for key, expr in params:
            if key == "or":
                pats = re.findall(r"(\w+)\.ilike\.\"\*(.*?)\*\"", expr)
                out = [
                    r
                    for r in out
                    if any(
                        p.replace("\\", "").lower() in str(r.get(c, "")).lower() for c, p in pats
                    )
                ]
        return out

    # -- request handler ----------------------------------------------------------------
    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail_with:
            return httpx.Response(self.fail_with, json={"message": "boom", "code": "X"})
        path = request.url.path.removeprefix("/rest/v1").strip("/")
        params = list(request.url.params.multi_items())
        if path == "":
            if not self.openapi:
                return httpx.Response(404, json={"message": "no"})
            return httpx.Response(200, json=self._spec())
        if path not in self.tables:
            return httpx.Response(
                404, json={"code": "PGRST205", "message": f"Could not find table {path}"}
            )
        rows = self.tables[path]
        pd = dict(params)
        if request.method == "GET":
            matched = self._filter(rows, params)
            headers = {"content-range": f"*/{len(matched)}"}
            off = int(pd.get("offset", 0))
            lim = int(pd.get("limit", len(matched)))
            return httpx.Response(200, json=matched[off : off + lim], headers=headers)
        if request.method == "POST":
            new = json.loads(request.content)
            rows.extend(new)
            return httpx.Response(201, json=new)
        if request.method == "PATCH":
            patch = json.loads(request.content)
            hit = self._filter(rows, params)
            for r in hit:
                r.update(patch)
            return httpx.Response(200, json=hit)
        if request.method == "DELETE":
            hit = self._filter(rows, params)
            self.tables[path] = [r for r in rows if r not in hit]
            return httpx.Response(200, json=hit)
        return httpx.Response(405)


def sample_tables() -> dict[str, list[dict[str, Any]]]:
    return {
        "notes": [
            {"id": 1, "title": "Alpha plan", "body": "ship the MCP server", "status": "open"},
            {"id": 2, "title": "Beta", "body": "write docs", "status": "open"},
            {"id": 3, "title": "Gamma", "body": "old stuff", "status": "done"},
        ],
        "secrets": [{"id": 1, "value": "hidden"}],
    }
