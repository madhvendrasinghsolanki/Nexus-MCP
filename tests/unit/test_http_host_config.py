from __future__ import annotations

import pytest

from nexus_mcp import http_app
from nexus_mcp.core.config import Settings
from nexus_mcp.http_app import transport_security


def test_extra_allowed_hosts_and_bound_host_are_trusted() -> None:
    s = Settings(
        _env_file=None,  # type: ignore[call-arg]
        NEXUS_TRANSPORT="streamable-http",
        NEXUS_HOST="10.0.0.5",
        NEXUS_PORT=9000,
        NEXUS_HTTP_AUTH_TOKEN="x" * 40,
        NEXUS_ALLOWED_HOSTS="mcp.example.com",
    )
    sec = transport_security(s)
    assert sec.enable_dns_rebinding_protection is True
    assert "10.0.0.5:9000" in sec.allowed_hosts
    assert "mcp.example.com" in sec.allowed_hosts
    assert "https://mcp.example.com" in sec.allowed_origins


def test_loopback_does_not_trust_arbitrary_hosts() -> None:
    sec = transport_security(Settings(_env_file=None))  # type: ignore[call-arg]
    assert all("example" not in h for h in sec.allowed_hosts)


def test_run_http_passes_host_port_and_quiet_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    import uvicorn

    seen: dict[str, object] = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(kw, app=app))
    monkeypatch.setattr(http_app, "build_http_app", lambda server, settings: "ASGI")
    http_app.run_http(object(), Settings(_env_file=None, NEXUS_PORT=1234))  # type: ignore[arg-type, call-arg]
    assert seen["app"] == "ASGI"
    assert seen["host"] == "127.0.0.1"
    assert seen["port"] == 1234
    assert seen["log_level"] == "warning"
