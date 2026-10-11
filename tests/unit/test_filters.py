import pytest
from pydantic import ValidationError

from nexus_mcp.providers.supabase.filters import Filter, OrderBy, escape_like_term


def f(**kw):  # type: ignore[no-untyped-def]
    return Filter(**kw)


def test_basic_ops() -> None:
    assert f(column="age", op="gte", value=18).to_param() == ("age", "gte.18")
    assert f(column="ok", value=True).to_param() == ("ok", "eq.true")
    assert f(column="x", op="is", value=None).to_param() == ("x", "is.null")
    assert f(column="name", op="ilike", value="*bob*").to_param() == ("name", "ilike.*bob*")


def test_in_quotes_special_characters() -> None:
    col, expr = f(column="t", op="in", value=["a,b", 'c"d', "e)"]).to_param()
    assert col == "t"
    assert expr == 'in.("a,b","c\\"d","e)")'


@pytest.mark.parametrize(
    "col", ["id&select", "a b", "1abc", "x;drop", "", "a" * 64, "order", "SELECT", "or"]
)
def test_bad_or_reserved_columns_rejected(col: str) -> None:
    with pytest.raises(ValidationError):
        f(column=col, value=1)


@pytest.mark.parametrize(
    "kw",
    [
        {"op": "in", "value": 5},
        {"op": "in", "value": []},
        {"op": "in", "value": list(range(101))},
        {"op": "is", "value": "maybe"},
        {"op": "eq", "value": None},
        {"op": "eq", "value": [1]},
        {"op": "like", "value": 3},
        {"op": "nope", "value": 1},
    ],
)
def test_bad_value_shapes_rejected(kw: dict) -> None:  # type: ignore[type-arg]
    with pytest.raises(ValidationError):
        f(column="c", **kw)


def test_extra_fields_forbidden() -> None:
    with pytest.raises(ValidationError):
        Filter(column="c", value=1, raw="x")  # type: ignore[call-arg]


def test_order_by_validates() -> None:
    assert OrderBy(column="created_at", direction="desc").direction == "desc"
    with pytest.raises(ValidationError):
        OrderBy(column="a,b")


def test_escape_like_term() -> None:
    assert escape_like_term("50%_off*") == "50\\%\\_off"
    assert escape_like_term("a\\b") == "a\\\\b"
    assert escape_like_term("tab\there") == "tabhere"
