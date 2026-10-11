"""Concurrent, timeout-bounded health checks across all providers."""

from __future__ import annotations

import asyncio
import time

from nexus_mcp.core.logging import get_logger
from nexus_mcp.core.models import HealthReport, SourceHealth
from nexus_mcp.providers.base import DataSource
from nexus_mcp.providers.registry import ProviderRegistry

log = get_logger(__name__)


async def _check(source: DataSource, timeout_s: float) -> SourceHealth:
    start = time.perf_counter()
    try:
        detail = await asyncio.wait_for(source.health(), timeout=timeout_s)
    except TimeoutError:
        return SourceHealth(source=source.name, ok=False, detail=f"timed out after {timeout_s}s")
    except Exception as exc:
        log.warning("health_check_failed", source=source.name, error=type(exc).__name__)
        return SourceHealth(source=source.name, ok=False, detail="probe failed")
    latency = round((time.perf_counter() - start) * 1000, 1)
    return SourceHealth(source=source.name, ok=True, latency_ms=latency, detail=detail)


async def check_health(registry: ProviderRegistry, timeout_s: float) -> HealthReport:
    results = await asyncio.gather(*(_check(s, timeout_s) for s in registry))
    return HealthReport(ok=all(r.ok for r in results), sources=list(results))
