import httpx
import pytest

from app.adapters.asterisk import AsteriskAdapterError, AsteriskHttpClient


@pytest.mark.asyncio
async def test_asterisk_adapter_maps_call_legs_and_bridge() -> None:
    requests: list[httpx.Request] = []
    ids = iter(["source-channel", "target-channel"])

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST" and request.url.path == "/ari/channels":
            return httpx.Response(200, json={"id": next(ids)})
        if request.method == "POST" and request.url.path == "/ari/bridges":
            return httpx.Response(200, json={"id": "bridge-1"})
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

    call_id = "call-1"
    source_channel = await adapter.originate("1001", "2001", call_id)
    target_channel = await adapter.originate_participant(call_id, "2001")
    await adapter.bridge_call(call_id)
    await adapter.mute(call_id, "2001")
    await adapter.unmute(call_id, "2001")

    assert source_channel == "source-channel"
    assert target_channel == "target-channel"
    assert requests[0].url.params["endpoint"] == "PJSIP/1001"
    assert requests[0].url.params["appArgs"] == f"source,{call_id},2001"
    assert requests[1].url.params["endpoint"] == "PJSIP/2001"
    assert requests[1].url.params["appArgs"] == f"callee,{call_id},1001"
    assert requests[2].url.path == "/ari/bridges"
    assert requests[3].url.path == "/ari/bridges/bridge-1/addChannel"
    assert requests[3].url.params["channel"] == "source-channel,target-channel"

    await adapter.cleanup_call(call_id, source_channel)
    await http_client.aclose()


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
