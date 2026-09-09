import asyncio

import httpx
import pytest
from uuid import UUID

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
    assert requests[1].url.params["appArgs"] == f"callee,{call_id},2001"
    assert requests[2].url.path == "/ari/bridges"
    assert requests[3].url.path == "/ari/bridges/tccs-call-1/addChannel"
    assert requests[3].url.params["channel"] == "source-channel,target-channel"

    await adapter.cleanup_call(call_id, source_channel)
    await http_client.aclose()


@pytest.mark.asyncio
async def test_asterisk_adapter_serializes_concurrent_bridge_updates() -> None:
    requests: list[httpx.Request] = []
    bridge_ready = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST" and request.url.path == "/ari/channels":
            endpoint = request.url.params["endpoint"]
            participant = endpoint.removeprefix("PJSIP/")
            return httpx.Response(200, json={"id": f"{participant}-channel"})
        if request.method == "POST" and request.url.path == "/ari/bridges":
            bridge_ready.set()
            return httpx.Response(200, json={"id": "bridge-1"})
        if request.method == "POST" and request.url.path == "/ari/bridges/tccs-call-concurrent/addChannel":
            return httpx.Response(204)
        return httpx.Response(204)

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    adapter = AsteriskHttpClient(
        base_url="http://asterisk.example/ari",
        username="tccs",
        password="secret",
        client=http_client,
    )

    call_id = "call-concurrent"
    await adapter.originate("1001", "2001", call_id)
    await adapter.originate_participant(call_id, "2001")
    await adapter.originate_participant(call_id, "2002")

    first = asyncio.create_task(adapter.bridge_call(call_id, "2001"))
    await bridge_ready.wait()
    second = asyncio.create_task(adapter.bridge_call(call_id, "2002"))
    await asyncio.gather(first, second)

    bridge_creations = [request for request in requests if request.url.path == "/ari/bridges"]
    add_requests = [
        request
        for request in requests
        if request.url.path == "/ari/bridges/tccs-call-concurrent/addChannel"
    ]
    assert len(bridge_creations) == 1
    assert len(add_requests) == 2
    assert add_requests[0].url.params["channel"] == "1001-channel,2001-channel"
    assert add_requests[1].url.params["channel"] == "2002-channel"

    await http_client.aclose()


@pytest.mark.asyncio
async def test_asterisk_adapter_keeps_conference_when_one_leg_ends() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST" and request.url.path == "/ari/channels":
            endpoint = request.url.params["endpoint"]
            participant = endpoint.removeprefix("PJSIP/")
            return httpx.Response(200, json={"id": f"{participant}-channel"})
        if request.method == "POST" and request.url.path == "/ari/bridges":
            return httpx.Response(200, json={"id": "bridge-1"})
        return httpx.Response(204)

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    adapter = AsteriskHttpClient(
        base_url="http://asterisk.example/ari",
        username="tccs",
        password="secret",
        client=http_client,
    )

    call_id = "call-three-way"
    source_channel = await adapter.originate("1001", "2001", call_id)
    first_channel = await adapter.originate_participant(call_id, "2001")
    second_channel = await adapter.originate_participant(call_id, "2002")
    await adapter.bridge_call(call_id, "2001")
    await adapter.bridge_call(call_id, "2002")

    await adapter.cleanup_call(call_id, first_channel)

    bridge_path = f"/ari/bridges/tccs-{call_id}"
    assert any(request.url.path == "/ari/bridges" for request in requests)
    assert not any(request.url.path == bridge_path and request.method == "DELETE" for request in requests)
    assert not any(request.url.path == f"/ari/channels/{source_channel}" and request.method == "DELETE" for request in requests)
    assert not any(request.url.path == f"/ari/channels/{second_channel}" and request.method == "DELETE" for request in requests)

    await adapter.cleanup_call(call_id, second_channel)
    assert any(request.url.path == bridge_path and request.method == "DELETE" for request in requests)
    assert any(request.url.path == f"/ari/channels/{source_channel}" and request.method == "DELETE" for request in requests)

    await http_client.aclose()


@pytest.mark.asyncio
async def test_asterisk_adapter_accepts_uuid_after_string_mapping() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/ari/channels":
            return httpx.Response(200, json={"id": "source-channel"})
        return httpx.Response(204)

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    adapter = AsteriskHttpClient(
        "http://asterisk.example/ari",
        "tccs",
        "secret",
        client=http_client,
    )

    call_id = "e6c7b8a5-c972-41b4-a1fd-403b72331b43"
    await adapter.originate("1001", "2001", call_id)
    channel_id = await adapter.originate_participant(UUID(call_id), "2001")

    assert channel_id == "source-channel"

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
