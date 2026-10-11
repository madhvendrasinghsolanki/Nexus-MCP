from __future__ import annotations

import pytest

from nexus_mcp.core.config import Settings
from nexus_mcp.core.errors import NotFound, ProviderError, ValidationFailed
from nexus_mcp.providers.registry import ProviderRegistry
from nexus_mcp.services.search import KnowledgeService
from tests.support.fakes import item, make_fake


def service(*sources, **env):  # type: ignore[no-untyped-def]
    reg = ProviderRegistry()
    for s in sources:
        reg.register(s)
    return KnowledgeService(reg, Settings(_env_file=None, **env)), reg


async def test_merges_and_ranks_across_sources() -> None:
    svc, _ = service(
        make_fake("notion", [item("notion", "n1", "Roadmap alpha")]),
        make_fake("supabase", [item("supabase", "t:1", "alpha roadmap", "q3")]),
    )
    res = await svc.search("alpha roadmap")
    assert {i.source for i in res.items} == {"notion", "supabase"}
    assert not res.partial and all(s.ok for s in res.sources)
    assert res.items[0].score >= res.items[1].score  # type: ignore[operator]


async def test_empty_results_are_successful_and_not_partial() -> None:
    svc, _ = service(make_fake("empty"))

    res = await svc.search("does-not-exist")

    assert res.items == [] and not res.partial
    assert res.sources[0].ok and res.sources[0].returned == 0


async def test_one_failing_source_yields_partial_results() -> None:
    svc, _ = service(
        make_fake("good", [item("good", "1", "hit")]),
        make_fake("bad", error=ProviderError("Could not reach it.")),
        make_fake("boom", error=RuntimeError("secret_abcdefgh1234 leaked")),
    )
    res = await svc.search("hit")
    by = {s.source: s for s in res.sources}
    assert res.partial and [i.id for i in res.items] == ["1"]
    assert "Could not reach" in (by["bad"].error or "")
    assert by["boom"].error == "failed unexpectedly" and "secret_" not in str(res)


async def test_slow_source_times_out_without_blocking_others() -> None:
    svc, _ = service(
        make_fake("fast", [item("fast", "1", "x")]),
        make_fake("slow", [item("slow", "2", "x")], delay=1.0),
        NEXUS_REQUEST_TIMEOUT_S=0.05,
    )
    res = await svc.search("x")
    assert res.partial and [i.id for i in res.items] == ["1"]
    assert next(s for s in res.sources if s.source == "slow").error == "timed out"


async def test_source_filter_and_validation() -> None:
    a, b = make_fake("a", [item("a", "1", "q")]), make_fake("b", [item("b", "2", "q")])
    svc, _ = service(a, b)
    assert [i.source for i in (await svc.search("q", ["b"])).items] == ["b"]
    assert len((await svc.search("q", ["a", "a"])).sources) == 1  # duplicates collapsed
    with pytest.raises(NotFound) as e:
        await svc.search("q", ["nope"])
    assert "a, b" in e.value.to_client_message()
    with pytest.raises(ValidationFailed):
        await service(make_fake("ro", searchable=False))[0].search("q", ["ro"])


@pytest.mark.parametrize("q", ["", "   ", "x" * 201])
async def test_bad_queries_rejected(q: str) -> None:
    svc, _ = service(make_fake("a"))
    with pytest.raises(ValidationFailed):
        await svc.search(q)


async def test_limit_clamped_and_forwarded_and_query_normalised() -> None:
    src = make_fake("a", [item("a", str(i), "q") for i in range(10)])
    svc, _ = service(src, NEXUS_MAX_RESULTS=3)
    res = await svc.search("  spaced   out ", limit=50)
    assert len(res.items) == 3 and res.query == "spaced out"
    assert src.calls[-1] == ("spaced out", 3)  # type: ignore[attr-defined]


async def test_unsearchable_sources_skipped_by_default() -> None:
    svc, _ = service(make_fake("ro", searchable=False), make_fake("a", [item("a", "1", "q")]))
    assert [s.source for s in (await svc.search("q")).sources] == ["a"]


async def test_get_delegates_and_validates() -> None:
    svc, _ = service(make_fake("a"))
    assert (await svc.get("a", " 7 ")).id == "7"
    with pytest.raises(NotFound):
        await svc.get("zzz", "1")
    with pytest.raises(ValidationFailed):
        await svc.get("a", "  ")
    with pytest.raises(ValidationFailed):
        await svc.get("a", "x" * 301)
