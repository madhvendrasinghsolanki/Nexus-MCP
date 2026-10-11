import io
import json

from nexus_mcp.core.errors import ErrorCode, NotFound, WriteNotPermitted
from nexus_mcp.core.logging import configure_logging, get_logger


def test_client_message_format() -> None:
    err = NotFound("nope", hint="try again")
    assert err.code is ErrorCode.NOT_FOUND
    assert err.to_client_message() == "[not_found] nope Hint: try again"
    assert WriteNotPermitted("x").to_client_message() == "[write_not_permitted] x"


def test_logs_go_to_stderr_and_redact(capsys) -> None:  # type: ignore[no-untyped-def]
    configure_logging("INFO", "json")
    get_logger("t").info("evt", api_key="abc", note="token secret_abcdefgh1234 here")
    captured = capsys.readouterr()
    assert captured.out == ""
    line = json.loads(captured.err.strip().splitlines()[-1])
    assert line["api_key"] == "***"
    assert "secret_abcdefgh1234" not in line["note"]
    _ = io


def test_http_client_request_logs_are_suppressed() -> None:
    import logging

    configure_logging("DEBUG", "console")
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
