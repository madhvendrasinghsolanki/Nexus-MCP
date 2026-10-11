"""Transport-level failure handling shared by both HTTP clients (no real network)."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from nexus_mcp.core.errors import NotFound, ProviderError, UpstreamTimeout, ValidationFailed
from nexus_mcp.providers.notion.client import NotionClient
from nexus_mcp.providers.supabase.client import PostgrestClient, auth_headers

Handler = Callable[[httpx.Request], httpx.Response]


def _notion(handler: Handler) -> NotionClient:
    return NotionClient("tok", "2022-06-28", 5.0, transport=httpx.MockTransport(handler))


def _supabase(handler: Handler) -> PostgrestClient:
    return PostgrestClient(
        "https://x.supabase.co", "key", "public", 5.0, transport=httpx.MockTransport(handler)
    )


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def instant(_: float) -> None:
        return None

    monkeypatch.setattr("asyncio.sleep", instant)


def _raise(exc: Exception) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return handler


@pytest.mark.parametrize("make", [_notion, _supabase])
async def test_timeout_maps_to_upstream_timeout(make: Callable[[Handler], object]) -> None:
    client = make(_raise(httpx.ReadTimeout("slow")))
    with pytest.raises(UpstreamTimeout):
        await client.request("GET", "/x")  # type: ignore[attr-defined]


@pytest.mark.parametrize("make", [_notion, _supabase])
async def test_connect_error_is_retried_once_for_get_then_mapped(
    make: Callable[[Handler], object],
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("boom https://secret-host.example")

    client = make(handler)
    with pytest.raises(ProviderError) as ei:
        await client.request("GET", "/x")  # type: ignore[attr-defined]
    assert calls == 2
    assert "secret-host" not in str(ei.value)


@pytest.mark.parametrize("make", [_notion, _supabase])
async def test_connect_error_not_retried_for_writes(make: Callable[[Handler], object]) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("boom")

    client = make(handler)
    with pytest.raises(ProviderError):
        await client.request("POST", "/x", json={})  # type: ignore[attr-defined]
    assert calls == 1  # a possibly-applied write must never be replayed


@pytest.mark.parametrize("make", [_notion, _supabase])
async def test_transient_get_failure_recovers_on_retry(make: Callable[[Handler], object]) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("blip")
        return httpx.Response(200, json={"ok": True} if make is _notion else [])

    client = make(handler)
    await client.request("GET", "/x")  # type: ignore[attr-defined]
    assert calls == 2


async def test_notion_rejects_non_object_json() -> None:
    client = _notion(lambda r: httpx.Response(200, json=[1, 2]))
    with pytest.raises(ProviderError):
        await client.request("GET", "/x")


async def test_notion_retry_after_garbage_falls_back() -> None:
    seq = [httpx.Response(429, headers={"retry-after": "soon"}), httpx.Response(200, json={})]
    client = _notion(lambda r: seq.pop(0))
    assert await client.request("GET", "/x") == {}


async def test_non_json_error_body_does_not_leak_or_crash() -> None:
    html = "<html>proxy error with internal-host.corp</html>"
    for client in (
        _notion(lambda r: httpx.Response(404, text=html)),
        _supabase(lambda r: httpx.Response(404, text=html)),
    ):
        with pytest.raises(NotFound) as ei:
            await client.request("GET", "/x")
        assert "internal-host" not in str(ei.value)


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (401, {}, ProviderError),
        (429, {}, ProviderError),
        (500, {}, ProviderError),
        (400, {"message": "bad"}, ValidationFailed),
        (404, {}, NotFound),
    ],
)
async def test_supabase_status_mapping(status: int, body: dict[str, str], expected: type) -> None:
    client = _supabase(lambda r: httpx.Response(status, json=body))
    with pytest.raises(expected):
        await client.request("POST", "/t", json={})


def test_supabase_publishable_keys_are_not_sent_as_bearer_tokens() -> None:
    assert auth_headers("sb_publishable_example") == {"apikey": "sb_publishable_example"}
    assert auth_headers("legacy-key") == {
        "apikey": "legacy-key",
        "Authorization": "Bearer legacy-key",
    }
