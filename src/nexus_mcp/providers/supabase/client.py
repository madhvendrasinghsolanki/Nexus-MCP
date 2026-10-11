"""Minimal async PostgREST client (Supabase REST API) with safe error mapping."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from nexus_mcp.core.errors import (
    NexusError,
    NotFound,
    ProviderError,
    UpstreamTimeout,
    ValidationFailed,
)
from nexus_mcp.core.logging import get_logger

log = get_logger(__name__)

_RETRY_STATUS = {502, 503, 504}
_MAX_DETAIL = 240


class AccessDenied(ProviderError):
    """HTTP 401/403: the key was refused for THIS request (not necessarily for every request)."""


def auth_headers(api_key: str) -> dict[str, str]:
    """Legacy anon/service keys are JWTs and also go in `Authorization`. The newer
    `sb_publishable_` / `sb_secret_` keys are NOT JWTs: sending them as a Bearer token makes the
    platform try to parse them as one and answer 401, so they travel in `apikey` only."""
    if api_key.startswith("sb_"):
        return {"apikey": api_key}
    return {"apikey": api_key, "Authorization": f"Bearer {api_key}"}


class PostgrestClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        schema: str,
        timeout_s: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._schema = schema
        self._http = httpx.AsyncClient(
            base_url=f"{base_url}/rest/v1",
            headers=auth_headers(api_key),
            timeout=httpx.Timeout(timeout_s),
            transport=transport,
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: list[tuple[str, str]] | None = None,
        json: Any = None,
        prefer: str | None = None,
        accept: str | None = None,
    ) -> httpx.Response:
        headers: dict[str, str] = {}
        profile = "Accept-Profile" if method in ("GET", "HEAD") else "Content-Profile"
        headers[profile] = self._schema
        if prefer:
            headers["Prefer"] = prefer
        if accept:
            headers["Accept"] = accept

        attempts = 2 if method == "GET" else 1
        for attempt in range(1, attempts + 1):
            try:
                resp = await self._http.request(
                    method,
                    path,
                    params=httpx.QueryParams(tuple(params)) if params else None,
                    json=json,
                    headers=headers,
                )
            except httpx.TimeoutException:
                raise UpstreamTimeout("Supabase did not respond in time.") from None
            except httpx.TransportError as exc:
                if attempt < attempts:
                    await asyncio.sleep(0.2)
                    continue
                log.warning("supabase_transport_error", error=type(exc).__name__)
                raise ProviderError("Could not reach Supabase.") from None
            if resp.status_code in _RETRY_STATUS and attempt < attempts:
                await asyncio.sleep(0.2)
                continue
            self._raise_for_status(resp)
            return resp
        raise ProviderError("Could not reach Supabase.")  # pragma: no cover

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> None:
        if resp.is_success:
            return
        status = resp.status_code
        body: dict[str, Any] = {}
        try:
            parsed = resp.json()
            if isinstance(parsed, dict):
                body = parsed
        except ValueError:
            pass
        code = str(body.get("code", ""))
        detail = str(body.get("message", ""))[:_MAX_DETAIL]
        log.info("supabase_error", status=status, pg_code=code)

        err: NexusError
        if status == 401 or status == 403 or code == "42501":
            err = AccessDenied(
                "Supabase denied access.",
                hint="Check SUPABASE_KEY and the table's Row Level Security policies.",
            )
        elif status == 404 or code in {"42P01", "PGRST205", "PGRST202"}:
            err = NotFound("Table or resource not found in Supabase.", hint=detail or None)
        elif status in (400, 409, 422) or code.startswith(("22", "23", "42", "PGRST1")):
            err = ValidationFailed(f"Supabase rejected the request: {detail or 'bad request'}")
        elif status == 429:
            err = ProviderError("Supabase rate limit hit.", hint="Retry shortly.")
        else:
            err = ProviderError(f"Supabase returned an error (HTTP {status}).")
        raise err from None