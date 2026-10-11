"""Real server process over HTTP with a token: auth, Host-header and content-type enforcement."""

import asyncio
import sys
from pathlib import Path

import httpx
import pytest

from tests.integration.test_http_transport import free_port, wait_listening

pytestmark = pytest.mark.integration
TOKEN = "s3cure-" + "x" * 40
SOURCE_ROOT = str(Path(__file__).resolve().parents[2] / "src")
INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "t", "version": "1"},
    },
}
HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}


async def test_http_requires_token_and_valid_host() -> None:
    port = free_port()
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "nexus_mcp", "--transport", "streamable-http",
        env={
            "NEXUS_DEMO_MODE": "true",
            "NEXUS_PORT": str(port),
            "NEXUS_HTTP_AUTH_TOKEN": TOKEN,
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": SOURCE_ROOT,
        },
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )  # fmt: skip
    url = f"http://127.0.0.1:{port}/mcp"
    try:
        await wait_listening(port)
        async with httpx.AsyncClient(timeout=10) as c:
            none = await c.post(url, json=INIT, headers=HEADERS)
            wrong = await c.post(
                url, json=INIT, headers={**HEADERS, "authorization": "Bearer nope"}
            )
            ok = await c.post(
                url, json=INIT, headers={**HEADERS, "authorization": f"Bearer {TOKEN}"}
            )
            rebind = await c.post(
                url,
                json=INIT,
                headers={**HEADERS, "authorization": f"Bearer {TOKEN}", "host": "evil.example"},
            )
        assert none.status_code == 401 and wrong.status_code == 401
        assert ok.status_code == 200
        assert rebind.status_code == 421  # DNS-rebinding protection still applies behind auth
    finally:
        proc.terminate()
        await asyncio.wait_for(proc.wait(), timeout=10)
