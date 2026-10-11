import asyncio

from nexus_mcp.providers.registry import ProviderRegistry
from nexus_mcp.services.health import check_health
from tests.conftest import FakeSource


class SlowSource(FakeSource):
    name = "slow"

    async def health(self) -> str | None:
        await asyncio.sleep(1)
        return None


async def test_healthy_and_failure_isolated() -> None:
    bad = FakeSource(healthy=False)
    bad.name = "bad"  # type: ignore[misc]
    reg = ProviderRegistry()
    reg.register(FakeSource())
    reg.register(bad)
    report = await check_health(reg, 1)
    by = {s.source: s for s in report.sources}
    assert by["fake"].ok and not by["bad"].ok and not report.ok
    assert "secret_" not in (by["bad"].detail or "")  # upstream error text is never exposed


async def test_timeout() -> None:
    reg = ProviderRegistry()
    reg.register(SlowSource())
    report = await check_health(reg, 0.05)
    assert not report.ok and "timed out" in (report.sources[0].detail or "")


async def test_empty_registry_is_ok() -> None:
    assert (await check_health(ProviderRegistry(), 1)).ok
