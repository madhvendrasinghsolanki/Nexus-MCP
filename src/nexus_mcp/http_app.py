"""Authenticated, hardened HTTP transport (streamable-http).

Layers, outermost first: bearer-token auth -> SDK transport security (Host/Origin checks against
DNS rebinding, JSON content-type, body-size limit, session caps) -> MCP app.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.types import ASGIApp, Receive, Scope, Send

from nexus_mcp.core.config import Settings
from nexus_mcp.core.logging import get_logger

log = get_logger(__name__)


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


class BearerAuthMiddleware:
    """Pure-ASGI static bearer-token check using constant-time comparison."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self._expected = _digest(token)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":  # lifespan / websocket pass through
            await self.app(scope, receive, send)
            return
            
        # Public, read-only health check for uptime monitoring.
        if scope.get("path") == "/health" and scope.get("method") == "GET":
            body = b'{"status":"ok"}'
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                        (b"cache-control", b"no-store"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        supplied = ""
        for name, value in scope.get("headers", []):
            if name == b"authorization":
                scheme, _, credential = value.decode("latin-1").partition(" ")
                if scheme.lower() == "bearer":
                    supplied = credential.strip()
                break
        if not hmac.compare_digest(_digest(supplied), self._expected):
            client = scope.get("client")
            log.warning(
                "http_auth_failed", client=client[0] if client else None, path=scope["path"]
            )
            body = json.dumps({"error": "unauthorized"}).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                        (b"www-authenticate", b'Bearer realm="nexus-mcp"'),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)


def transport_security(settings: Settings) -> TransportSecuritySettings:
    hosts = {f"127.0.0.1:{settings.port}", f"localhost:{settings.port}", f"[::1]:{settings.port}"}
    origins = {f"http://{h}" for h in hosts}
    if settings.host not in ("127.0.0.1", "localhost", "::1"):
        hosts.add(f"{settings.host}:{settings.port}")
    for extra in settings.allowed_host_list:
        hosts.add(extra)
        origins.add(f"https://{extra}")
        origins.add(f"http://{extra}")
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=sorted(hosts),
        allowed_origins=sorted(origins),
    )


def build_http_app(server: MCPServer, settings: Settings) -> ASGIApp:
    app: Any = server.streamable_http_app(
        host=settings.host, transport_security=transport_security(settings)
    )
    if settings.http_auth_token is not None:
        return BearerAuthMiddleware(app, settings.http_auth_token.get_secret_value())
    return app  # type: ignore[no-any-return]


def run_http(server: MCPServer, settings: Settings) -> None:
    import uvicorn

    uvicorn.run(
        build_http_app(server, settings),
        host=settings.host,
        port=settings.port,
        log_level="warning",  # access logs would record request paths; our logger handles events
    )
