from __future__ import annotations

from nexus_mcp.core.config import Settings
from nexus_mcp.providers.supabase.provider import SupabaseSource


def create_supabase_source(settings: Settings) -> SupabaseSource | None:
    """Factory: returns None (provider disabled) unless URL and key are both set."""
    return SupabaseSource(settings) if settings.supabase_configured else None


__all__ = ["SupabaseSource", "create_supabase_source"]
