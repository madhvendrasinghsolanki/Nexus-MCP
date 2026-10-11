"""The DataSource contract every provider (Supabase, Notion, ...) implements."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from nexus_mcp.core.errors import ProviderError
from nexus_mcp.core.models import (
    Capability,
    KnowledgeDocument,
    KnowledgeItem,
    SourceInfo,
    SourceKind,
)


class DataSource(ABC):
    """Port for a knowledge source. Tools depend on this, never on a concrete SDK."""

    name: ClassVar[str]
    kind: ClassVar[SourceKind]
    description: ClassVar[str] = ""

    @property
    def capabilities(self) -> frozenset[Capability]:
        return frozenset({Capability.READ, Capability.SEARCH})

    def info(self) -> SourceInfo:
        return SourceInfo(
            name=self.name,
            kind=self.kind,
            capabilities=sorted(self.capabilities, key=lambda c: c.value),
            description=self.description,
        )

    def supports(self, capability: Capability) -> bool:
        return capability in self.capabilities

    @abstractmethod
    async def health(self) -> str | None:
        """Cheap connectivity probe. Return an optional safe detail; raise on failure."""

    @abstractmethod
    async def search(self, query: str, limit: int) -> list[KnowledgeItem]:
        """Free-text search returning normalized items."""

    async def get(self, item_id: str) -> KnowledgeDocument:
        """Fetch one item by the id returned from `search`."""
        raise self._unsupported(Capability.READ)

    async def aclose(self) -> None:  # noqa: B027 - optional hook
        """Release network resources."""

    def _unsupported(self, capability: Capability) -> ProviderError:
        return ProviderError(f"Source '{self.name}' does not support '{capability.value}'.")
