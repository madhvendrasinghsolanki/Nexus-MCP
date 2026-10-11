"""Spawn the real CLI over stdio: proves logs never corrupt the protocol stream."""

import sys

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

pytestmark = pytest.mark.integration


async def test_real_stdio_server_process() -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "nexus_mcp"],
        env={"NEXUS_LOG_LEVEL": "DEBUG", "PATH": "/usr/bin:/bin"},
    )
    async with Client(params) as client:
        result = await client.call_tool("server_info", {})
    assert not result.is_error
    assert result.structured_content["name"] == "nexus-mcp"


def test_embedding_without_configure_logging_never_writes_stdout() -> None:
    import subprocess

    code = (
        "from nexus_mcp.core.logging import get_logger\n"
        "get_logger('x').warning('hello', token='abc')\n"
        "import nexus_mcp.server\n"
    )
    out = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=30
    )
    assert out.stdout == "" and "hello" in out.stderr and "abc" not in out.stderr
