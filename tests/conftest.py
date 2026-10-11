from __future__ import annotations

from typing import ClassVar

import pytest

from nexus_mcp.core.config import Settings
from nexus_mcp.core.models import Capability, KnowledgeItem, SourceKind
from nexus_mcp.providers.base import DataSource
from nexus_mcp.providers.registry import ProviderRegistry


class FakeSource(DataSource):
    name: ClassVar[str] = "fake"
    kind: ClassVar[SourceKind] = SourceKind.NOTION
    description: ClassVar[str] = "In-memory fake"

    def __init__(self, healthy: bool = True) -> None:
        self.healthy = healthy

    async def health(self) -> str | None:
        if not self.healthy:
            raise RuntimeError("secret_abcdefgh1234 leaked in upstream error")
        return "ok"

    async def search(self, query: str, limit: int) -> list[KnowledgeItem]:
        return [KnowledgeItem(source=self.name, id="1", title=query)][:limit]


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


@pytest.fixture
def registry() -> ProviderRegistry:
    reg = ProviderRegistry()
    reg.register(FakeSource())
    return reg


__all__ = ["Capability", "FakeSource"]
