import pytest
from pydantic import ValidationError

from nexus_mcp.core.config import Settings, get_settings


def make(**env: object) -> Settings:
    return Settings(_env_file=None, **env)  # type: ignore[arg-type]


def test_defaults_are_safe() -> None:
    s = make()
    assert not s.allow_writes and not s.allow_deletes
    assert not s.supabase_configured and not s.notion_configured


def test_deletes_require_writes() -> None:
    with pytest.raises(ValidationError):
        make(NEXUS_ALLOW_DELETES=True)
    assert make(NEXUS_ALLOW_DELETES=True, NEXUS_ALLOW_WRITES=True).allow_deletes


def test_public_summary_never_leaks_secrets() -> None:
    s = make(
        NOTION_TOKEN="secret_topsecretvalue",
        SUPABASE_URL="https://x.supabase.co",
        SUPABASE_KEY="eyJhbGciOi.payload.sig",
    )
    dumped = str(s.public_summary())
    assert "topsecretvalue" not in dumped and "eyJ" not in dumped
    assert s.notion_configured and s.supabase_configured


def test_secrets_are_masked_in_repr() -> None:
    assert "topsecretvalue" not in repr(make(NOTION_TOKEN="secret_topsecretvalue"))


def test_blank_secret_values_are_treated_as_unset() -> None:
    s = make(NEXUS_HTTP_AUTH_TOKEN="  ", SUPABASE_KEY="", NOTION_TOKEN="\t")
    assert s.http_auth_token is None
    assert not s.supabase_configured and not s.notion_configured


def test_invalid_port_rejected() -> None:
    with pytest.raises(ValidationError):
        make(NEXUS_PORT=0)


def test_supabase_url_rules() -> None:
    assert make(SUPABASE_URL="https://x.supabase.co/").supabase_url == "https://x.supabase.co"
    assert make(SUPABASE_URL="http://localhost:54321").supabase_url == "http://localhost:54321"
    for bad in ("http://evil.example.com", "ftp://x.co", "https://user:pw@x.co"):
        with pytest.raises(ValidationError):
            make(SUPABASE_URL=bad)


def test_table_policy_parsing_and_validation() -> None:
    s = make(
        SUPABASE_ALLOWED_TABLES="notes, docs",
        SUPABASE_WRITABLE_TABLES="notes",
        SUPABASE_SEARCH_COLUMNS="notes:title|body,docs:title",
    )
    assert s.supabase_allowed_table_set == {"notes", "docs"}
    assert s.supabase_search_map == {"notes": ("title", "body"), "docs": ("title",)}
    for key, bad in (
        ("SUPABASE_ALLOWED_TABLES", "notes;drop"),
        ("SUPABASE_SEARCH_COLUMNS", "notes"),
        ("SUPABASE_SCHEMA", "pub lic"),
    ):
        with pytest.raises(ValidationError):
            make(**{key: bad})


def test_notion_policy_parsing() -> None:
    pid = "11111111-1111-1111-1111-111111111111"
    s = make(
        NOTION_WRITABLE_PARENTS=f"{pid.replace('-', '').upper()}, https://notion.so/x-{'2' * 32}"
    )
    assert s.notion_writable_set == {pid, "22222222-2222-2222-2222-222222222222"}
    assert s.public_summary()["notion_writable_parents"] == sorted(s.notion_writable_set)
    with pytest.raises(ValidationError):
        make(NOTION_WRITABLE_PARENTS="not-an-id")
    with pytest.raises(ValidationError):
        make(NOTION_API_VERSION="latest")


def test_allowed_hosts_and_warnings_are_safe() -> None:
    s = make(
        NEXUS_TRANSPORT="streamable-http",
        NEXUS_ALLOWED_HOSTS=" mcp.example.com, , proxy.example.com ",
    )
    assert s.allowed_host_list == ["mcp.example.com", "proxy.example.com"]
    assert any("without authentication" in warning for warning in s.warnings)

    writes = make(NEXUS_ALLOW_WRITES=True)
    assert any("no writable" in warning for warning in writes.warnings)
    assert any("AUDIT" in warning for warning in writes.warnings)


def test_get_settings_is_cached_and_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("NEXUS_MAX_RESULTS", "7")
    try:
        first = get_settings()
        second = get_settings()
        assert first is second and first.max_results == 7
    finally:
        get_settings.cache_clear()
