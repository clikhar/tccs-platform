import pytest
import httpx

from app.adapters.asterisk import AsteriskAdapterError, AsteriskHttpClient


@pytest.mark.asyncio
async def test_asterisk_adapter_maps_ari_channel_and_participant() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST" and request.url.path == "/ari/channels":
            return httpx.Response(200, json={"id": "ari-channel-1"})
        return httpx.Response(204)

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    adapter = AsteriskHttpClient(
        base_url="http://asterisk.example/ari",
        username="tccs",
        password="secret",
        app="tccs-core",
        client=http_client,
    )

    channel_id = await adapter.originate("1001", "2001")
    await adapter.mute(channel_id, "2001")
    await adapter.unmute(channel_id, "2001")
    await adapter.remove_participant(channel_id, "2001")
    await adapter.hangup(channel_id)
    await http_client.aclose()

    assert channel_id == "ari-channel-1"
    assert [request.method for request in requests] == [
        "POST",
        "POST",
        "DELETE",
        "DELETE",
        "DELETE",
    ]
    assert requests[0].url.path == "/ari/channels"
    assert requests[0].url.params["endpoint"] == "PJSIP/2001"
    assert requests[0].url.params["app"] == "tccs-core"
    assert requests[0].url.params["appArgs"] == "outbound,1001,2001"
    assert requests[1].url.path == "/ari/channels/ari-channel-1/mute"
    assert requests[1].url.params["direction"] == "both"
    assert requests[2].url.path == "/ari/channels/ari-channel-1/mute"
    assert requests[2].url.params["direction"] == "both"
    assert requests[3].url.path == "/ari/channels/ari-channel-1"
    assert requests[4].url.path == "/ari/channels/ari-channel-1"


@pytest.mark.asyncio
async def test_asterisk_adapter_rejects_unknown_participant() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(204))
    http_client = httpx.AsyncClient(transport=transport)
    adapter = AsteriskHttpClient("http://asterisk.example/ari", "tccs", "secret", client=http_client)

    with pytest.raises(AsteriskAdapterError, match="not mapped"):
        await adapter.mute("missing-call", "2001")

    await http_client.aclose()


@pytest.mark.asyncio
async def test_asterisk_adapter_wraps_ari_http_errors() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="channel not found")

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    adapter = AsteriskHttpClient("http://asterisk.example/ari", "tccs", "secret", client=http_client)

    with pytest.raises(AsteriskAdapterError, match="HTTP 404: channel not found"):
        await adapter.hangup("missing-channel")

    await http_client.aclose()
