import httpx
import pytest

from app.core_client import CoreClientError, TCCSCoreClient


@pytest.mark.asyncio
async def test_core_client_creates_individual_call(monkeypatch):
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "call_id": "call-1",
                "state": "initiated",
                "source": "9999",
                "target": "1002",
                "conference_id": None,
            },
        )

    transport = httpx.MockTransport(handler)
    original_async_client = httpx.AsyncClient

    def client_factory(**kwargs):
        return original_async_client(transport=transport, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)

    client = TCCSCoreClient("http://core:8080")
    result = await client.create_call(source="9999", target="1002", section_id="1")

    assert result["call_id"] == "call-1"
    assert requests[0].url.path == "/api/v1/calls"
    assert requests[0].method == "POST"
    assert requests[0].read() == b'{"source":"9999","target":"1002","mode":"individual","section_id":"1"}'


@pytest.mark.asyncio
async def test_core_client_raises_on_core_error(monkeypatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, json={"detail": "Asterisk unavailable"})

    transport = httpx.MockTransport(handler)
    original_async_client = httpx.AsyncClient

    def client_factory(**kwargs):
        return original_async_client(transport=transport, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)

    client = TCCSCoreClient("http://core:8080")
    with pytest.raises(CoreClientError, match="Asterisk unavailable"):
        await client.create_call(source="9999", target="1002")
