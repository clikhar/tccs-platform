import httpx
import pytest

from app.core_client import CoreClientError, TCCSCoreClient


@pytest.mark.asyncio
async def test_core_client_creates_individual_call():
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
    client = TCCSCoreClient("http://core:8080")
    async with httpx.AsyncClient(transport=transport) as http_client:
        original = httpx.AsyncClient
        httpx.AsyncClient = lambda **kwargs: http_client
        try:
            result = await client.create_call(source="9999", target="1002", section_id="1")
        finally:
            httpx.AsyncClient = original

    assert result["call_id"] == "call-1"
    assert requests[0].url.path == "/api/v1/calls"
    assert requests[0].method == "POST"
    assert requests[0].read() == b'{"source":"9999","target":"1002","mode":"individual","section_id":"1"}'


@pytest.mark.asyncio
async def test_core_client_raises_on_core_error():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, json={"detail": "Asterisk unavailable"})

    transport = httpx.MockTransport(handler)
    client = TCCSCoreClient("http://core:8080")
    async with httpx.AsyncClient(transport=transport) as http_client:
        original = httpx.AsyncClient
        httpx.AsyncClient = lambda **kwargs: http_client
        try:
            with pytest.raises(CoreClientError, match="Asterisk unavailable"):
                await client.create_call(source="9999", target="1002")
        finally:
            httpx.AsyncClient = original
