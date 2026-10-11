from __future__ import annotations

import json

import pytest

from nexus_mcp import cli


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    monkeypatch.chdir(tmp_path)  # type: ignore[arg-type]  # no stray .env
    for k in ("NEXUS_TRANSPORT", "NEXUS_HOST", "NEXUS_HTTP_AUTH_TOKEN", "NEXUS_DEMO_MODE"):
        monkeypatch.delenv(k, raising=False)


def test_check_config_prints_secret_free_summary(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NOTION_TOKEN", "ntn_supersecretvalue")
    assert cli.main(["--check-config"]) == 0
    out = capsys.readouterr().out
    assert json.loads(out)["notion_configured"] is True
    assert "supersecret" not in out


def test_invalid_config_exits_2_without_echoing_values(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NEXUS_HTTP_AUTH_TOKEN", "short-but-secret")
    assert cli.main(["--check-config"]) == 2
    err = capsys.readouterr().err
    assert "Invalid configuration" in err
    assert "short-but-secret" not in err


def test_transport_flag_cannot_bypass_exposure_check(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NEXUS_HOST", "0.0.0.0")  # noqa: S104
    assert cli.main(["--transport", "streamable-http"]) == 2


def test_runs_stdio_server(monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[str] = []

    class FakeServer:
        def run(self, transport: str) -> None:
            ran.append(transport)

    monkeypatch.setattr(cli, "create_server", lambda settings: FakeServer())
    assert cli.main([]) == 0
    assert ran == ["stdio"]


def test_runs_http_server(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[object] = []
    monkeypatch.setattr(cli, "create_server", lambda settings: "srv")
    monkeypatch.setattr(cli, "run_http", lambda server, settings: seen.append(server))
    assert cli.main(["--transport", "streamable-http"]) == 0
    assert seen == ["srv"]
