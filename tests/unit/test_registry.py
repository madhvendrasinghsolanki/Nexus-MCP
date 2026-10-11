import pytest

from nexus_mcp.core.config import Settings
from nexus_mcp.core.errors import NotFound
from nexus_mcp.providers.registry import ProviderRegistry, build_registry
from tests.conftest import FakeSource


def test_register_get_and_duplicate() -> None:
    reg = ProviderRegistry()
    reg.register(FakeSource())
    assert reg.get("fake").name == "fake" and len(reg) == 1
    with pytest.raises(ValueError):
        reg.register(FakeSource())


def test_unknown_source_lists_available() -> None:
    reg = ProviderRegistry()
    reg.register(FakeSource())
    with pytest.raises(NotFound) as exc:
        reg.get("missing")
    assert "fake" in exc.value.to_client_message()


def test_factories_skip_unconfigured() -> None:
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    reg = build_registry(s, [lambda _s: None, lambda _s: FakeSource()])
    assert [x.name for x in reg] == ["fake"]


def test_source_info_and_capabilities() -> None:
    info = FakeSource().info()
    assert info.name == "fake" and {c.value for c in info.capabilities} == {"read", "search"}
