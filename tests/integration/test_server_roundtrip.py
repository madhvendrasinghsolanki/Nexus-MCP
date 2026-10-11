"""Full MCP round trip using the real client against the in-process server."""

from __future__ import annotations

import pytest
from mcp import Client

from nexus_mcp.providers.registry import ProviderRegistry
from nexus_mcp.server import create_server
from tests.conftest import FakeSource

pytestmark = pytest.mark.integration


class ClosingSource(FakeSource):
    name = "closing"

    def __init__(self) -> None:
        super().__init__()
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


async def test_tools_listed_with_read_only_annotations(settings, registry) -> None:  # type: ignore[no-untyped-def]
    async with Client(create_server(settings, registry)) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert {"server_info", "list_sources", "health_check"} <= set(tools)
    assert all(t.annotations and t.annotations.read_only_hint for t in tools.values())
    assert all(t.output_schema for t in tools.values())  # structured outputs advertised


async def test_server_info_structured(settings, registry) -> None:  # type: ignore[no-untyped-def]
    async with Client(create_server(settings, registry)) as client:
        result = await client.call_tool("server_info", {})
    data = result.structured_content
    assert not result.is_error
    assert data["name"] == "nexus-mcp"
    assert data["config"]["writes_enabled"] is False
    assert data["sources"][0]["name"] == "fake"


async def test_health_check_reports_failure_without_leaking(settings) -> None:  # type: ignore[no-untyped-def]
    from nexus_mcp.providers.registry import ProviderRegistry

    reg = ProviderRegistry()
    reg.register(FakeSource(healthy=False))
    async with Client(create_server(settings, reg)) as client:
        result = await client.call_tool("health_check", {})
    assert result.structured_content["ok"] is False
    assert "secret_" not in str(result.structured_content)


async def test_works_with_zero_providers(settings) -> None:  # type: ignore[no-untyped-def]
    from nexus_mcp.providers.registry import ProviderRegistry

    async with Client(create_server(settings, ProviderRegistry())) as client:
        result = await client.call_tool("list_sources", {})
    assert not result.is_error


async def test_server_lifespan_closes_registered_providers(settings) -> None:  # type: ignore[no-untyped-def]
    source = ClosingSource()
    registry = ProviderRegistry()
    registry.register(source)

    async with Client(create_server(settings, registry)) as client:
        assert not (await client.call_tool("server_info", {})).is_error

    assert source.closed
