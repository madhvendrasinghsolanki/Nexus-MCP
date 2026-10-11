"""Typed, environment-driven configuration. No secret ever lives in code."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from nexus_mcp.core.errors import ValidationFailed
from nexus_mcp.core.validation import parse_notion_id, validate_identifier

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _csv_identifiers(raw: str, what: str) -> tuple[str, ...]:
    return tuple(validate_identifier(p.strip(), what) for p in raw.split(",") if p.strip())


class Settings(BaseSettings):
    """All runtime configuration. Loaded from environment variables / `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Server
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field("INFO", alias="NEXUS_LOG_LEVEL")
    log_format: Literal["console", "json"] = Field("console", alias="NEXUS_LOG_FORMAT")
    transport: Literal["stdio", "streamable-http"] = Field("stdio", alias="NEXUS_TRANSPORT")
    host: str = Field("127.0.0.1", alias="NEXUS_HOST")
    port: int = Field(8000, ge=1, le=65535, alias="NEXUS_PORT")
    request_timeout_s: float = Field(15.0, gt=0, le=120, alias="NEXUS_REQUEST_TIMEOUT_S")
    max_results: int = Field(25, ge=1, le=200, alias="NEXUS_MAX_RESULTS")
    max_write_rows: int = Field(50, ge=1, le=1000, alias="NEXUS_MAX_WRITE_ROWS")

    # HTTP transport security
    http_auth_token: SecretStr | None = Field(None, alias="NEXUS_HTTP_AUTH_TOKEN")
    allowed_hosts: str = Field("", alias="NEXUS_ALLOWED_HOSTS")  # extra Host values, comma list

    # Abuse limits (0 disables a rate limit)
    rate_limit_reads_per_min: int = Field(
        120, ge=0, le=100_000, alias="NEXUS_RATE_LIMIT_READS_PER_MIN"
    )
    rate_limit_writes_per_min: int = Field(
        30, ge=0, le=100_000, alias="NEXUS_RATE_LIMIT_WRITES_PER_MIN"
    )
    tool_timeout_s: float = Field(30.0, gt=0, le=300, alias="NEXUS_TOOL_TIMEOUT_S")
    max_payload_bytes: int = Field(100_000, ge=1_000, le=5_000_000, alias="NEXUS_MAX_PAYLOAD_BYTES")
    max_cell_chars: int = Field(4_000, ge=100, le=100_000, alias="NEXUS_MAX_CELL_CHARS")
    audit_log_path: str | None = Field(None, alias="NEXUS_AUDIT_LOG_PATH")

    # Safety gates (writes are opt-in; deletes need their own opt-in)
    allow_writes: bool = Field(False, alias="NEXUS_ALLOW_WRITES")
    allow_deletes: bool = Field(False, alias="NEXUS_ALLOW_DELETES")

    # Providers (optional: a provider is enabled only when its credentials exist)
    supabase_url: str | None = Field(None, alias="SUPABASE_URL")
    supabase_key: SecretStr | None = Field(None, alias="SUPABASE_KEY")
    notion_token: SecretStr | None = Field(None, alias="NOTION_TOKEN")

    # Demo mode: fictional in-memory sources, no credentials needed
    demo_mode: bool = Field(False, alias="NEXUS_DEMO_MODE")

    # Notion
    notion_api_version: str = Field(
        "2022-06-28", alias="NOTION_API_VERSION", pattern=r"^\d{4}-\d{2}-\d{2}$"
    )
    # Comma-separated page/database IDs (or URLs). Writes are limited to pages whose direct
    # parent is listed here (or pages listed explicitly).
    notion_writable_parents: str = Field("", alias="NOTION_WRITABLE_PARENTS")

    # Supabase access policy. Comma-separated table names; empty allowlist = all readable tables.
    supabase_schema: str = Field("public", alias="SUPABASE_SCHEMA")
    supabase_allowed_tables: str = Field("", alias="SUPABASE_ALLOWED_TABLES")
    supabase_writable_tables: str = Field("", alias="SUPABASE_WRITABLE_TABLES")
    # Format: "notes:title|body,docs:title" (table:col1|col2) -- feeds unified search
    supabase_search_columns: str = Field("", alias="SUPABASE_SEARCH_COLUMNS")

    @field_validator("supabase_url")
    @classmethod
    def _supabase_url_safe(cls, v: str | None) -> str | None:
        if not v:
            return None
        parsed = urlparse(v)
        local = parsed.hostname in _LOCAL_HOSTS
        if parsed.scheme != "https" and not (parsed.scheme == "http" and local):
            raise ValueError("SUPABASE_URL must use https (http allowed only for localhost)")
        if not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("SUPABASE_URL must be a plain host URL without credentials")
        return v.rstrip("/")

    @field_validator("http_auth_token", "supabase_key", "notion_token", mode="before")
    @classmethod
    def _blank_secret_is_unset(cls, v: object) -> object:
        """A blank `.env` value (e.g. `NEXUS_HTTP_AUTH_TOKEN=`) means "not set", on any version."""
        return None if isinstance(v, str) and not v.strip() else v

    @field_validator("http_auth_token")
    @classmethod
    def _token_strength(cls, v: SecretStr | None) -> SecretStr | None:
        if v is not None and len(v.get_secret_value()) < 32:
            raise ValueError("NEXUS_HTTP_AUTH_TOKEN must be at least 32 characters")
        return v

    @model_validator(mode="after")
    def _validate_http_exposure(self) -> Settings:
        if (
            self.transport == "streamable-http"
            and self.host not in _LOCAL_HOSTS
            and self.http_auth_token is None
        ):
            raise ValueError(
                "Refusing to serve HTTP on a non-loopback host without NEXUS_HTTP_AUTH_TOKEN"
            )
        return self

    @model_validator(mode="after")
    def _validate_policy(self) -> Settings:
        if self.allow_deletes and not self.allow_writes:
            raise ValueError("NEXUS_ALLOW_DELETES requires NEXUS_ALLOW_WRITES=true")
        try:
            validate_identifier(self.supabase_schema, "SUPABASE_SCHEMA")
            _ = self.supabase_allowed_table_set, self.supabase_writable_table_set
            _ = self.supabase_search_map
            _ = self.notion_writable_set
        except ValidationFailed as exc:
            raise ValueError(exc.message) from None
        return self

    @property
    def supabase_allowed_table_set(self) -> frozenset[str]:
        return frozenset(
            _csv_identifiers(self.supabase_allowed_tables, "table in SUPABASE_ALLOWED_TABLES")
        )

    @property
    def supabase_writable_table_set(self) -> frozenset[str]:
        return frozenset(
            _csv_identifiers(self.supabase_writable_tables, "table in SUPABASE_WRITABLE_TABLES")
        )

    @property
    def allowed_host_list(self) -> list[str]:
        return [h.strip() for h in self.allowed_hosts.split(",") if h.strip()]

    @property
    def warnings(self) -> list[str]:
        """Non-fatal misconfigurations worth telling the operator about at startup."""
        out: list[str] = []
        if self.allow_writes and not (self.supabase_writable_table_set or self.notion_writable_set):
            out.append("NEXUS_ALLOW_WRITES is on but no writable tables/parents are configured.")
        if self.allow_writes and not self.audit_log_path:
            out.append("Writes are enabled without NEXUS_AUDIT_LOG_PATH; no durable audit trail.")
        if self.transport == "streamable-http" and self.http_auth_token is None:
            out.append("HTTP transport is running without authentication (loopback only).")
        return out

    @property
    def notion_writable_set(self) -> frozenset[str]:
        parts = (p.strip() for p in self.notion_writable_parents.split(","))
        return frozenset(parse_notion_id(p, "ID in NOTION_WRITABLE_PARENTS") for p in parts if p)

    @property
    def supabase_search_map(self) -> dict[str, tuple[str, ...]]:
        out: dict[str, tuple[str, ...]] = {}
        for entry in filter(None, (e.strip() for e in self.supabase_search_columns.split(","))):
            table, sep, cols = entry.partition(":")
            if not sep or not cols:
                raise ValidationFailed(
                    f"Bad SUPABASE_SEARCH_COLUMNS entry {entry!r}.", hint="Use table:col1|col2"
                )
            out[validate_identifier(table.strip(), "search table")] = tuple(
                validate_identifier(c.strip(), "search column")
                for c in cols.split("|")
                if c.strip()
            )
        return out

    @property
    def supabase_configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_key)

    @property
    def notion_configured(self) -> bool:
        return bool(self.notion_token)

    def public_summary(self) -> dict[str, object]:
        """Config safe to show to a client: never includes secret values."""
        return {
            "transport": self.transport,
            "writes_enabled": self.allow_writes,
            "deletes_enabled": self.allow_deletes,
            "max_results": self.max_results,
            "max_write_rows": self.max_write_rows,
            "supabase_writable_tables": sorted(self.supabase_writable_table_set),
            "notion_writable_parents": sorted(self.notion_writable_set),
            "request_timeout_s": self.request_timeout_s,
            "demo_mode": self.demo_mode,
            "http_auth_enabled": self.http_auth_token is not None,
            "audit_log_enabled": bool(self.audit_log_path),
            "rate_limits_per_min": {
                "reads": self.rate_limit_reads_per_min,
                "writes": self.rate_limit_writes_per_min,
            },
            "supabase_configured": self.supabase_configured,
            "notion_configured": self.notion_configured,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
