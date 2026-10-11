from __future__ import annotations

import httpx
import pytest
from pydantic import ValidationError

from nexus_mcp.cli import main
from nexus_mcp.core.config import Settings
from nexus_mcp.http_app import BearerAuthMiddleware, transport_security

TOKEN = "t" * 40


async def downstream(scope, receive, send) -> None:  # type: ignore[no-untyped-def]
    if scope["type"] == "lifespan":
        while True:
            msg = await receive()
            if msg["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            else:
                await send({"type": "lifespan.shutdown.complete"})
                return
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def client() -> httpx.AsyncClient:
    app = BearerAuthMiddleware(downstream, TOKEN)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"authorization": ""},
        {"authorization": "Bearer"},
        {"authorization": "Bearer wrong"},
        {"authorization": f"Basic {TOKEN}"},
        {"authorization": TOKEN},
        {"authorization": f"Bearer {TOKEN}x"},
        {"authorization": f"Bearer {TOKEN[:-1]}"},
    ],
)  # fmt: skip
async def test_rejects_missing_or_wrong_credentials(headers: dict[str, str]) -> None:
    async with client() as c:
        r = await c.post("/mcp", headers=headers)
    assert r.status_code == 401
    assert r.headers["www-authenticate"].startswith("Bearer")
    assert TOKEN not in r.text


async def test_accepts_correct_token_case_insensitive_scheme() -> None:
    async with client() as c:
        for scheme in ("Bearer", "bearer", "BEARER"):
            r = await c.post("/mcp", headers={"authorization": f"{scheme} {TOKEN}"})
            assert r.status_code == 200 and r.text == "ok"


async def test_lifespan_scope_is_not_blocked() -> None:
    sent: list[str] = []
    msgs = iter([{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])

    async def receive():  # type: ignore[no-untyped-def]
        return next(msgs)

    async def send(m):  # type: ignore[no-untyped-def]
        sent.append(m["type"])

    await BearerAuthMiddleware(downstream, TOKEN)({"type": "lifespan"}, receive, send)
    assert sent == ["lifespan.startup.complete", "lifespan.shutdown.complete"]


# ---- configuration rules ----------------------------------------------------------------------
def make(**env: object) -> Settings:
    return Settings(_env_file=None, **env)  # type: ignore[arg-type]


def test_token_must_be_strong() -> None:
    with pytest.raises(ValidationError):
        make(NEXUS_HTTP_AUTH_TOKEN="short")
    assert make(NEXUS_HTTP_AUTH_TOKEN=TOKEN).public_summary()["http_auth_enabled"] is True


def test_refuses_network_exposure_without_token() -> None:
    with pytest.raises(ValidationError, match="NEXUS_HTTP_AUTH_TOKEN"):
        make(NEXUS_TRANSPORT="streamable-http", NEXUS_HOST="0.0.0.0")  # noqa: S104
    make(NEXUS_TRANSPORT="streamable-http", NEXUS_HOST="0.0.0.0", NEXUS_HTTP_AUTH_TOKEN=TOKEN)  # noqa: S104
    make(NEXUS_TRANSPORT="streamable-http")  # loopback without a token is allowed (with a warning)
    make(NEXUS_HOST="0.0.0.0")  # noqa: S104  # stdio never listens, so the host is irrelevant


def test_cli_transport_flag_cannot_bypass_the_exposure_rule(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("NEXUS_HOST", "0.0.0.0")  # noqa: S104
    monkeypatch.delenv("NEXUS_HTTP_AUTH_TOKEN", raising=False)
    assert main(["--transport", "streamable-http", "--check-config"]) == 2
    err = capsys.readouterr().err
    assert "NEXUS_HTTP_AUTH_TOKEN" in err


def test_transport_security_allows_only_expected_hosts() -> None:
    ts = transport_security(make(NEXUS_PORT=9000))
    assert ts.enable_dns_rebinding_protection and "127.0.0.1:9000" in ts.allowed_hosts
    assert "evil.com" not in ts.allowed_hosts
    ts2 = transport_security(make(NEXUS_PORT=9000, NEXUS_ALLOWED_HOSTS="mcp.example.com"))
    assert (
        "mcp.example.com" in ts2.allowed_hosts and "https://mcp.example.com" in ts2.allowed_origins
    )


def test_warnings() -> None:
    assert make().warnings == []
    w = make(NEXUS_ALLOW_WRITES=True).warnings
    assert any("no writable" in x for x in w) and any("AUDIT" in x for x in w)
    assert any(
        "without authentication" in x for x in make(NEXUS_TRANSPORT="streamable-http").warnings
    )
