"""Unified knowledge service: concurrent fan-out, partial-failure tolerance, ranking."""

from __future__ import annotations

import asyncio
import time

from nexus_mcp.core.config import Settings
from nexus_mcp.core.errors import NexusError, ValidationFailed
from nexus_mcp.core.logging import get_logger
from nexus_mcp.core.models import (
    Capability,
    KnowledgeDocument,
    KnowledgeItem,
    SourceSearchStatus,
    UnifiedSearchResult,
)
from nexus_mcp.core.sanitize import scan_for_injection
from nexus_mcp.providers.base import DataSource
from nexus_mcp.providers.registry import ProviderRegistry
from nexus_mcp.services.ranking import rank

log = get_logger(__name__)

MAX_QUERY_CHARS = 200


def _flags(text: str) -> list[str]:
    hits = scan_for_injection(text)
    return ["possible_prompt_injection", *hits] if hits else []


class KnowledgeService:
    def __init__(self, registry: ProviderRegistry, settings: Settings) -> None:
        self._registry = registry
        self._settings = settings

    def _select(self, names: list[str] | None) -> list[DataSource]:
        if names:
            chosen = [self._registry.get(n) for n in dict.fromkeys(names)]
            for src in chosen:
                if not src.supports(Capability.SEARCH):
                    raise ValidationFailed(f"Source '{src.name}' does not support search.")
            return chosen
        return [s for s in self._registry if s.supports(Capability.SEARCH)]

    async def _one(
        self, src: DataSource, query: str, limit: int
    ) -> tuple[SourceSearchStatus, list[KnowledgeItem]]:
        start = time.perf_counter()
        try:
            items = await asyncio.wait_for(
                src.search(query, limit), timeout=self._settings.request_timeout_s
            )
        except TimeoutError:
            error = "timed out"
        except NexusError as exc:
            error = exc.to_client_message()
        except Exception as exc:
            log.error(
                "source_search_crash", source=src.name, error=type(exc).__name__, exc_info=True
            )
            error = "failed unexpectedly"
        else:
            ms = round((time.perf_counter() - start) * 1000, 1)
            return SourceSearchStatus(
                source=src.name, ok=True, returned=len(items), latency_ms=ms
            ), items
        log.warning("source_search_failed", source=src.name, reason=error)
        return SourceSearchStatus(source=src.name, ok=False, error=error), []

    async def search(
        self, query: str, sources: list[str] | None = None, limit: int = 10
    ) -> UnifiedSearchResult:
        query = " ".join(query.split())
        if not query:
            raise ValidationFailed("`query` must not be empty.")
        if len(query) > MAX_QUERY_CHARS:
            raise ValidationFailed(f"`query` is longer than {MAX_QUERY_CHARS} characters.")
        limit = max(1, min(limit, self._settings.max_results))
        selected = self._select(sources)
        results = await asyncio.gather(*(self._one(s, query, limit) for s in selected))
        statuses = [status for status, _ in results]
        merged = [item for _, items in results for item in items]
        ranked = [
            item.model_copy(update={"flags": _flags(f"{item.title}\n{item.snippet or ''}")})
            for item in rank(query, merged)[:limit]
        ]
        return UnifiedSearchResult(
            query=query,
            items=ranked,
            sources=statuses,
            partial=any(not s.ok for s in statuses),
        )

    async def get(self, source: str, item_id: str) -> KnowledgeDocument:
        src = self._registry.get(source)
        if not item_id.strip() or len(item_id) > 300:
            raise ValidationFailed("Invalid item id.")
        doc = await src.get(item_id.strip())
        return doc.model_copy(update={"flags": _flags(f"{doc.title}\n{doc.content}")})
