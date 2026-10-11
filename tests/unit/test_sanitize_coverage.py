"""Guard rail: every model field that can carry external text must pass through a sanitizer.

Adding a new text field to a provider-facing model without Safe* typing fails this test.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from nexus_mcp.core.models import KnowledgeDocument, KnowledgeItem
from nexus_mcp.providers.notion.models import (
    NotionDatabase,
    NotionObject,
    NotionPage,
    NotionRow,
    NotionWriteResult,
    PropertyInfo,
)
from nexus_mcp.providers.supabase.models import ColumnInfo, QueryResult, TableInfo, WriteResult

# Fields that are identifiers, URLs, enums or server-authored text (never external prose).
TRUSTED = {
    "id",
    "url",
    "type",
    "source",
    "kind",
    "table",
    "operation",
    "dry_run",
    "message",
    "database_id",
    "last_edited_time",
    "next_cursor",
    "has_more",
    "archived",
    "parent",
    "page_id",
    "returned",
    "limit",
    "offset",
    "clipped_cells",
    "matched_count",
    "affected_count",
    "content_truncated",
    "skipped_blocks",
    "flags",
    "score",
    "columns_inferred",
    "is_primary_key",
    "nullable",
    "columns",
    "items",
    "rows_count",
}

MODELS: list[type[BaseModel]] = [
    KnowledgeItem,
    KnowledgeDocument,
    NotionObject,
    NotionPage,
    PropertyInfo,
    NotionDatabase,
    NotionRow,
    NotionWriteResult,
    ColumnInfo,
    TableInfo,
    QueryResult,
    WriteResult,
]

# Containers whose children are themselves checked models.
NESTED = {("NotionDatabase", "properties"), ("NotionQueryResult", "rows"), ("TableInfo", "columns")}


@pytest.mark.parametrize("model", MODELS, ids=lambda m: m.__name__)
def test_every_external_text_field_is_sanitized(model: type[BaseModel]) -> None:
    unprotected = []
    for name, field in model.model_fields.items():
        if name in TRUSTED or (model.__name__, name) in NESTED:
            continue
        marker = repr(field.annotation) + repr(field.metadata)
        if "sanitize" not in marker:
            unprotected.append(name)
    assert not unprotected, f"{model.__name__} has unsanitized fields: {unprotected}"
