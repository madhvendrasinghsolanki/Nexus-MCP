"""Configurable in-memory DataSource for service-level tests."""

from __future__ import annotations

import asyncio
from typing import ClassVar

from nexus_mcp.core.errors import ProviderError
from nexus_mcp.core.models import KnowledgeDocument, KnowledgeItem, SourceKind
from nexus_mcp.providers.base import DataSource


def make_fake(
    name: str,
    items: list[KnowledgeItem] | None = None,
    *,
    delay: float = 0.0,
    error: Exception | None = None,
    searchable: bool = True,
) -> DataSource:
    class _Fake(DataSource):
        kind: ClassVar[SourceKind] = SourceKind.NOTION
        description: ClassVar[str] = f"fake {name}"
        calls: ClassVar[list[tuple[str, int]]] = []

        @property
        def capabilities(self):  # type: ignore[no-untyped-def]
            from nexus_mcp.core.models import Capability

            return frozenset(
                {Capability.READ, Capability.SEARCH} if searchable else {Capability.READ}
            )

        async def health(self) -> str | None:
            return "ok"

        async def search(self, query: str, limit: int) -> list[KnowledgeItem]:
            self.calls.append((query, limit))
            if delay:
                await asyncio.sleep(delay)
            if error:
                raise error
            return list(items or [])

        async def get(self, item_id: str) -> KnowledgeDocument:
            if error:
                raise error
            return KnowledgeDocument(source=name, id=item_id, title="T", content="body")

    _Fake.name = name  # type: ignore[misc]
    return _Fake()


def item(
    source: str, id_: str, title: str, snippet: str | None = None, **kw: object
) -> KnowledgeItem:
    return KnowledgeItem(source=source, id=id_, title=title, snippet=snippet, **kw)  # type: ignore[arg-type]


__all__ = ["ProviderError", "item", "make_fake"]
