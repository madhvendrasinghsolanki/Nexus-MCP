"""Foundation tools: introspection and health. Always available, even with no providers."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from nexus_mcp import __version__
from nexus_mcp.core.models import HealthReport, ServerInfo, SourceInfo
from nexus_mcp.services.health import check_health
from nexus_mcp.tools._common import READ_ONLY, READ_ONLY_LOCAL, App, guarded


def register(mcp: MCPServer, app: App) -> None:
    @mcp.tool(
        name="server_info",
        title="Server info",
        description=(
            "Describe this server: version, non-secret configuration (including whether "
            "writes/deletes are enabled) and the connected data sources. Call this first."
        ),
        annotations=READ_ONLY_LOCAL,
    )
    @guarded(app)
    async def server_info() -> ServerInfo:
        return ServerInfo(
            name="nexus-mcp",
            version=__version__,
            config=app.settings.public_summary(),
            sources=[s.info() for s in app.registry],
        )

    @mcp.tool(
        name="list_sources",
        title="List data sources",
        description="List the configured data sources and what each one supports.",
        annotations=READ_ONLY_LOCAL,
    )
    @guarded(app)
    async def list_sources() -> list[SourceInfo]:
        return [s.info() for s in app.registry]

    @mcp.tool(
        name="health_check",
        title="Health check",
        description="Probe every data source concurrently and report status and latency.",
        annotations=READ_ONLY,
    )
    @guarded(app)
    async def health_check() -> HealthReport:
        return await check_health(app.registry, app.settings.request_timeout_s)
