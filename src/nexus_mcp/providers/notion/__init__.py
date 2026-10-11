from __future__ import annotations

from nexus_mcp.core.config import Settings
from nexus_mcp.providers.notion.provider import NotionSource


def create_notion_source(settings: Settings) -> NotionSource | None:
    """Factory: returns None (provider disabled) unless NOTION_TOKEN is set."""
    return NotionSource(settings) if settings.notion_configured else None


__all__ = ["NotionSource", "create_notion_source"]
