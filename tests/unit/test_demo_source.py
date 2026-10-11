import pytest

from nexus_mcp.core.config import Settings
from nexus_mcp.core.errors import NotFound
from nexus_mcp.providers.demo.data import TICKETS, WIKI
from nexus_mcp.providers.demo.provider import DemoTickets, DemoWiki, create_demo_wiki
from nexus_mcp.providers.registry import build_registry


def test_off_by_default_and_on_with_flag() -> None:
    assert create_demo_wiki(Settings(_env_file=None)) is None
    reg = build_registry(Settings(_env_file=None, NEXUS_DEMO_MODE=True))
    assert [s.name for s in reg] == ["demo_wiki", "demo_tickets"]


def test_ids_are_unique() -> None:
    ids = [d.id for d in (*WIKI, *TICKETS)]
    assert len(ids) == len(set(ids))


async def test_search_ranks_by_token_hits_and_respects_limit() -> None:
    wiki = DemoWiki()
    res = await wiki.search("atlas beta roadmap", 3)
    assert res[0].id == "wiki-001" and len(res) <= 3
    assert await wiki.search("zzzz", 5) == [] and await wiki.search("   ", 5) == []
    assert res[0].metadata["last_edited_time"].endswith("Z")


async def test_get_and_not_found() -> None:
    doc = await DemoTickets().get("tkt-103")
    assert doc.fields["status"] == "todo" and "threat model" in doc.content
    with pytest.raises(NotFound):
        await DemoWiki().get("nope")


async def test_injection_canary_is_present_but_labelled_as_fixture() -> None:
    doc = await DemoWiki().get("wiki-005")
    assert "ignore all previous instructions" in doc.content.lower()
    assert "test fixture" in doc.title.lower() and "test-fixture" in doc.fields["tags"]
