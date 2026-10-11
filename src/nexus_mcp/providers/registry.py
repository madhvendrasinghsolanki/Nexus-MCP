"""Registry + factory wiring. Adding a provider = one factory entry; the server never changes."""

from __future__ import annotations

from collections.abc import Callable, Iterator

from nexus_mcp.core.config import Settings
from nexus_mcp.core.errors import NotFound
from nexus_mcp.core.logging import get_logger
from nexus_mcp.providers.base import DataSource

log = get_logger(__name__)

ProviderFactory = Callable[[Settings], DataSource | None]
"""Returns a provider, or None when its credentials are not configured."""


def _default_factories() -> list[ProviderFactory]:
    from nexus_mcp.providers.demo.provider import create_demo_tickets, create_demo_wiki
    from nexus_mcp.providers.notion import create_notion_source
    from nexus_mcp.providers.supabase import create_supabase_source

    return [create_supabase_source, create_notion_source, create_demo_wiki, create_demo_tickets]


class ProviderRegistry:
    def __init__(self) -> None:
        self._sources: dict[str, DataSource] = {}

    def register(self, source: DataSource) -> None:
        if source.name in self._sources:
            raise ValueError(f"Duplicate source name: {source.name}")
        self._sources[source.name] = source

    def get(self, name: str) -> DataSource:
        try:
            return self._sources[name]
        except KeyError:
            available = ", ".join(sorted(self._sources)) or "none configured"
            raise NotFound(
                f"Unknown source '{name}'.", hint=f"Available sources: {available}."
            ) from None

    def __iter__(self) -> Iterator[DataSource]:
        return iter(self._sources.values())

    def __contains__(self, name: object) -> bool:
        return name in self._sources

    def __len__(self) -> int:
        return len(self._sources)

    async def aclose(self) -> None:
        for source in self:
            await source.aclose()


def build_registry(
    settings: Settings, factories: list[ProviderFactory] | None = None
) -> ProviderRegistry:
    registry = ProviderRegistry()
    for factory in factories if factories is not None else _default_factories():
        source = factory(settings)
        if source is None:
            continue
        registry.register(source)
        log.info("provider_registered", source=source.name)
    return registry
