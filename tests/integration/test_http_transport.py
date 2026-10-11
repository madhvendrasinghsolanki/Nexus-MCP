"""The streamable-http transport: what remote / URL-based MCP clients connect to."""

import asyncio
import socket
import sys
from pathlib import Path

import pytest
from mcp import Client

pytestmark = pytest.mark.integration
SOURCE_ROOT = str(Path(__file__).resolve().parents[2] / "src")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def wait_listening(port: int, timeout_s: float = 20) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
        try:
            _, writer = await asyncio.open_connection("127.0.0.1", port)
        except OSError:
            await asyncio.sleep(0.1)
            continue
        writer.close()
        await writer.wait_closed()
        return
    pytest.fail("server did not start listening")


async def test_streamable_http_serves_tools_at_mcp_path() -> None:
    port = free_port()
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "nexus_mcp",
        "--transport",
        "streamable-http",
        env={
            "NEXUS_DEMO_MODE": "true",
            "NEXUS_PORT": str(port),
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": SOURCE_ROOT,
        },
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await wait_listening(port)
        async with Client(f"http://127.0.0.1:{port}/mcp") as client:
            names = {t.name for t in (await client.list_tools()).tools}
            result = await client.call_tool("search_knowledge", {"query": "postgresql"})
        assert {"search_knowledge", "get_item"} <= names
        assert result.structured_content["items"][0]["id"] == "wiki-002"
    finally:
        proc.terminate()
        await asyncio.wait_for(proc.wait(), timeout=10)
