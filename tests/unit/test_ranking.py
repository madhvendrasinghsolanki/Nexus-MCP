from nexus_mcp.services.ranking import MATCH_FLOOR, rank, score_item, tokens
from tests.support.fakes import item


def test_tokens_are_lowercase_words() -> None:
    assert tokens("Hello, MCP-server_v2!") == ["hello", "mcp", "server_v2"]


def test_title_beats_snippet_beats_nothing() -> None:
    q = "mcp server"
    title = score_item(q, item("s", "1", "MCP server design"))
    snippet = score_item(q, item("s", "2", "Notes", "about the mcp server"))
    none = score_item(q, item("s", "3", "Groceries"))
    assert title > snippet > none == MATCH_FLOOR


def test_exact_phrase_and_prefix_matching() -> None:
    assert score_item("mcp server", item("s", "1", "mcp server")) == 1.0
    assert score_item("serv", item("s", "1", "Server notes")) > MATCH_FLOOR
    assert (
        score_item("server mcp", item("s", "1", "mcp server")) < 1.0
    )  # word order matters for phrase


def test_blank_query_gets_floor_and_provider_score_wins() -> None:
    assert score_item("   ", item("s", "1", "x")) == MATCH_FLOOR
    assert score_item("x", item("s", "1", "x", score=0.33)) == 0.33


def test_rank_orders_and_scores_everything() -> None:
    items = [
        item("a", "1", "Unrelated"),
        item("b", "2", "alpha plan"),
        item("a", "3", "plan alpha beta"),
    ]
    out = rank("alpha plan", items)
    assert [i.id for i in out] == ["2", "3", "1"]
    assert all(i.score is not None for i in out)


def test_rank_dedupes_by_url_and_by_source_id() -> None:
    items = [
        item("notion", "1", "Plan", url="https://x/y"),
        item("web", "9", "Plan alpha", url="https://x/y"),
        item("a", "5", "Plan"),
        item("a", "5", "Plan"),
    ]
    out = rank("plan alpha", items)
    assert len(out) == 2 and out[0].title == "Plan alpha"  # higher-scoring duplicate kept


def test_ties_break_on_recency_then_provider_order() -> None:
    old = item("a", "1", "same", metadata={"last_edited_time": "2020-01-01T00:00:00.000Z"})
    new = item("b", "2", "same", metadata={"last_edited_time": "2026-01-01T00:00:00.000Z"})
    bad = item("c", "3", "same", metadata={"last_edited_time": "garbage"})
    assert [i.id for i in rank("same", [old, bad, new])] == ["2", "1", "3"]
    assert [i.id for i in rank("same", [item("a", "x", "same"), item("a", "y", "same")])] == [
        "x",
        "y",
    ]


def test_missing_snippet_is_not_penalised() -> None:
    no_snippet = score_item("alpha plan", item("notion", "1", "alpha plan"))
    with_snippet = score_item("alpha plan", item("supabase", "2", "alpha plan", "unrelated words"))
    assert no_snippet == 1.0 and no_snippet >= with_snippet
