from __future__ import annotations

import sys
from pathlib import Path
from typing import ClassVar

import pytest

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from nexus_mcp.core.config import Settings
from nexus_mcp.core.models import Capability, KnowledgeItem, SourceKind
from nexus_mcp.providers.base import DataSource
from nexus_mcp.providers.registry import ProviderRegistry

_RUNTIME_ENV_KEYS = (
    "NEXUS_LOG_LEVEL",
    "NEXUS_LOG_FORMAT",
    "NEXUS_TRANSPORT",
    "NEXUS_HOST",
    "NEXUS_PORT",
    "NEXUS_REQUEST_TIMEOUT_S",
    "NEXUS_MAX_RESULTS",
    "NEXUS_MAX_WRITE_ROWS",
    "NEXUS_HTTP_AUTH_TOKEN",
    "NEXUS_ALLOWED_HOSTS",
    "NEXUS_RATE_LIMIT_READS_PER_MIN",
    "NEXUS_RATE_LIMIT_WRITES_PER_MIN",
    "NEXUS_TOOL_TIMEOUT_S",
    "NEXUS_MAX_PAYLOAD_BYTES",
    "NEXUS_MAX_CELL_CHARS",
    "NEXUS_AUDIT_LOG_PATH",
    "NEXUS_ALLOW_WRITES",
    "NEXUS_ALLOW_DELETES",
    "SUPABASE_URL",
    "SUPABASE_KEY",
    "NOTION_TOKEN",
    "NEXUS_DEMO_MODE",
    "NOTION_API_VERSION",
    "NOTION_WRITABLE_PARENTS",
    "SUPABASE_SCHEMA",
    "SUPABASE_ALLOWED_TABLES",
    "SUPABASE_WRITABLE_TABLES",
    "SUPABASE_SEARCH_COLUMNS",
)


@pytest.fixture(autouse=True)
def isolate_runtime_configuration(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
) -> None:
    """Keep local dotenv files and credentials out of deterministic tests.

    Live tests are explicitly opt-in and are the only tests allowed to observe the
    operator's environment. All other tests run from an empty temporary directory
    with every application setting removed, including subprocesses.
    """
    if request.node.get_closest_marker("live") is not None:
        return
    monkeypatch.chdir(tmp_path)  # type: ignore[arg-type]
    for key in _RUNTIME_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


class FakeSource(DataSource):
    name: ClassVar[str] = "fake"
    kind: ClassVar[SourceKind] = SourceKind.NOTION
    description: ClassVar[str] = "In-memory fake"

    def __init__(self, healthy: bool = True) -> None:
        self.healthy = healthy

    async def health(self) -> str | None:
        if not self.healthy:
            raise RuntimeError("secret_abcdefgh1234 leaked in upstream error")
        return "ok"

    async def search(self, query: str, limit: int) -> list[KnowledgeItem]:
        return [KnowledgeItem(source=self.name, id="1", title=query)][:limit]


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


@pytest.fixture
def registry() -> ProviderRegistry:
    reg = ProviderRegistry()
    reg.register(FakeSource())
    return reg


__all__ = ["SOURCE_ROOT", "Capability", "FakeSource"]
