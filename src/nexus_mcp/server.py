"""Server factory. Pure wiring: settings + registry + tool modules."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.mcpserver import MCPServer

from nexus_mcp import __version__
from nexus_mcp.core.config import Settings, get_settings
from nexus_mcp.core.ratelimit import RateLimiter
from nexus_mcp.providers.registry import ProviderRegistry, build_registry
from nexus_mcp.services.audit import AuditLog
from nexus_mcp.tools import knowledge, notion, supabase, system
from nexus_mcp.tools._common import App

INSTRUCTIONS = (
    "Nexus exposes the user's Notion and Supabase data. Call `server_info` first to see which "
    "sources are connected and whether writes are enabled. Prefer read/search tools; write and "
    "delete tools are disabled unless the operator enabled them and always require confirmation."
)

TOOL_MODULES = [system, supabase, notion, knowledge]  # knowledge must come after providers


def create_server(
    settings: Settings | None = None, registry: ProviderRegistry | None = None
) -> MCPServer:
    settings = settings or get_settings()
    registry = registry if registry is not None else build_registry(settings)
    app = App(
        settings=settings,
        registry=registry,
        limiter=RateLimiter.from_settings(settings),
        audit=AuditLog.from_settings(settings),
    )

    @asynccontextmanager
    async def lifespan(_: MCPServer) -> AsyncIterator[dict[str, object]]:
        try:
            yield {}
        finally:
            await registry.aclose()  # release HTTP connections on shutdown

    mcp = MCPServer(
        name="nexus-mcp", version=__version__, instructions=INSTRUCTIONS, lifespan=lifespan
    )
    for module in TOOL_MODULES:
        module.register(mcp, app)
    return mcp
