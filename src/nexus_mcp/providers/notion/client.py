"""Minimal async Notion REST client with rate-limit handling and safe error mapping."""

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

BASE_URL = "https://api.notion.com/v1"
_MAX_DETAIL = 240
_MAX_RETRY_WAIT_S = 3.0


class NotionClient:
    def __init__(
        self,
        token: str,
        api_version: str,
        timeout_s: float,
        transport: httpx.AsyncBaseTransport | None = None,
        base_url: str = BASE_URL,
    ) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}", "Notion-Version": api_version},
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
        json: Any = None,
        params: dict[str, str | int] | None = None,
        idempotent: bool = False,
    ) -> dict[str, Any]:
        """`idempotent` marks safe-to-retry calls (GETs and read-only POSTs like search/query)."""
        safe = idempotent or method == "GET"
        attempts = 2 if safe else 1
        for attempt in range(1, attempts + 1):
            try:
                resp = await self._http.request(method, path, json=json, params=params)
            except httpx.TimeoutException:
                raise UpstreamTimeout("Notion did not respond in time.") from None
            except httpx.TransportError as exc:
                if attempt < attempts:
                    await asyncio.sleep(0.2)
                    continue
                log.warning("notion_transport_error", error=type(exc).__name__)
                raise ProviderError("Could not reach Notion.") from None
            if resp.status_code in (429, 502, 503, 504) and attempt < attempts:
                await asyncio.sleep(self._retry_wait(resp))
                continue
            self._raise_for_status(resp)
            data = resp.json()
            if not isinstance(data, dict):
                raise ProviderError("Unexpected response from Notion.")
            return data
        raise ProviderError("Could not reach Notion.")  # pragma: no cover

    @staticmethod
    def _retry_wait(resp: httpx.Response) -> float:
        try:
            return min(float(resp.headers.get("retry-after", "0.5")), _MAX_RETRY_WAIT_S)
        except ValueError:
            return 0.5

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
        log.info("notion_error", status=status, notion_code=code)

        err: NexusError
        if status == 401:
            err = ProviderError("Notion rejected the token.", hint="Check NOTION_TOKEN.")
        elif status == 403:
            err = ProviderError(
                "The Notion integration is not allowed to do that.",
                hint="Check the integration's capabilities and that the page is shared with it.",
            )
        elif status == 404:
            err = NotFound(
                "Notion object not found.",
                hint="It may not be shared with the integration (Share > Connections).",
            )
        elif status in (400, 409) or code in {"validation_error", "invalid_request_url"}:
            err = ValidationFailed(f"Notion rejected the request: {detail or 'bad request'}")
        elif status == 429:
            err = ProviderError("Notion rate limit hit.", hint="Retry shortly.")
        else:
            err = ProviderError(f"Notion returned an error (HTTP {status}).")
        raise err from None
