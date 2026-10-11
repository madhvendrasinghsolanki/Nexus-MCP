"""In-memory demo sources: lets every client, test and eval run with zero credentials."""

from __future__ import annotations

from typing import ClassVar

from nexus_mcp.core.config import Settings
from nexus_mcp.core.errors import NotFound
from nexus_mcp.core.models import KnowledgeDocument, KnowledgeItem, SourceKind
from nexus_mcp.providers.base import DataSource
from nexus_mcp.providers.demo.data import TICKETS, WIKI, DemoDoc
from nexus_mcp.services.ranking import tokens


class _DemoSource(DataSource):
    kind: ClassVar[SourceKind] = SourceKind.DEMO
    docs: ClassVar[tuple[DemoDoc, ...]] = ()

    async def health(self) -> str | None:
        return f"demo data ({len(self.docs)} items)"

    def _item(self, d: DemoDoc) -> KnowledgeItem:
        return KnowledgeItem(
            source=self.name,
            id=d.id,
            title=d.title,
            snippet=d.content[:160],
            metadata={"last_edited_time": f"{d.updated}T00:00:00Z", **d.fields},
        )

    async def search(self, query: str, limit: int) -> list[KnowledgeItem]:
        q = tokens(query)
        if not q:
            return []
        scored: list[tuple[int, int, DemoDoc]] = []
        for pos, d in enumerate(self.docs):
            words = tokens(d.title + " " + d.content)
            hits = sum(1 for t in q if any(w.startswith(t) for w in words))
            if hits:
                scored.append((-hits, pos, d))
        return [self._item(d) for _, _, d in sorted(scored)[:limit]]

    async def get(self, item_id: str) -> KnowledgeDocument:
        for d in self.docs:
            if d.id == item_id:
                return KnowledgeDocument(
                    source=self.name, id=d.id, title=d.title, content=d.content,
                    fields=d.fields, metadata={"updated": d.updated},
                )  # fmt: skip
        raise NotFound(f"No item '{item_id}' in '{self.name}'.")


class DemoWiki(_DemoSource):
    name: ClassVar[str] = "demo_wiki"
    description: ClassVar[str] = "Demo wiki pages (fictional, read-only)"
    docs: ClassVar[tuple[DemoDoc, ...]] = WIKI


class DemoTickets(_DemoSource):
    name: ClassVar[str] = "demo_tickets"
    description: ClassVar[str] = "Demo issue tracker rows (fictional, read-only)"
    docs: ClassVar[tuple[DemoDoc, ...]] = TICKETS


def create_demo_wiki(settings: Settings) -> DemoWiki | None:
    return DemoWiki() if settings.demo_mode else None


def create_demo_tickets(settings: Settings) -> DemoTickets | None:
    return DemoTickets() if settings.demo_mode else None
