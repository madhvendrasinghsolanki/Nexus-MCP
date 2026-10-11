"""Every Notion property type flattens to plain JSON (or None) without raising."""

from __future__ import annotations

from typing import Any

import pytest

from nexus_mcp.providers.notion.convert import flatten_property

RT = [{"plain_text": "hi"}]


@pytest.mark.parametrize(
    ("prop", "expected"),
    [
        ({"type": "title", "title": RT}, "hi"),
        ({"type": "number", "number": 3}, 3),
        ({"type": "select", "select": {"name": "A"}}, "A"),
        ({"type": "select", "select": None}, None),
        ({"type": "multi_select", "multi_select": [{"name": "a"}, {"name": "b"}]}, ["a", "b"]),
        ({"type": "date", "date": None}, None),
        ({"type": "date", "date": {"start": "2026-01-01", "end": None}}, "2026-01-01"),
        (
            {"type": "date", "date": {"start": "2026-01-01", "end": "2026-01-02"}},
            {"start": "2026-01-01", "end": "2026-01-02"},
        ),
        ({"type": "people", "people": [{"name": "Ann"}, {"id": "u2"}]}, ["Ann", "u2"]),
        ({"type": "relation", "relation": [{"id": "r1"}]}, ["r1"]),
        ({"type": "files", "files": [{"name": "a.pdf"}]}, ["a.pdf"]),
        ({"type": "formula", "formula": {"type": "number", "number": 7}}, 7),
        ({"type": "formula", "formula": None}, None),
        ({"type": "rollup", "rollup": None}, None),
        ({"type": "rollup", "rollup": {"type": "number", "number": 4}}, 4),
        (
            {
                "type": "rollup",
                "rollup": {"type": "array", "array": [{"type": "number", "number": 1}]},
            },
            [1],
        ),
        (
            {"type": "rollup", "rollup": {"type": "date", "date": {"start": "2026-02-02"}}},
            "2026-02-02",
        ),
        ({"type": "created_by", "created_by": {"name": "Bo"}}, "Bo"),
        ({"type": "last_edited_by", "last_edited_by": {"id": "u9"}}, "u9"),
        ({"type": "unique_id", "unique_id": {"prefix": "TASK", "number": 5}}, "TASK-5"),
        ({"type": "unique_id", "unique_id": {"prefix": None, "number": 5}}, "5"),
        ({"type": "unique_id", "unique_id": None}, None),
        ({"type": "some_future_type", "some_future_type": {}}, None),
        ({}, None),
    ],
)
def test_flatten(prop: dict[str, Any], expected: Any) -> None:
    assert flatten_property(prop) == expected
