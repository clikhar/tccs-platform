import json

import pytest

from app.adapters.asterisk_events import AsteriskEventStream


def test_builds_secure_ari_websocket_url() -> None:
    stream = AsteriskEventStream(
        base_url="https://asterisk.example.test:8089/",
        username="ari-user",
        password="secret",
        app="tccs-core",
    )

    assert stream.url == (
        "wss://asterisk.example.test:8089/ari/events?"
        "api_key=ari-user%3Asecret&app=tccs-core"
    )


@pytest.mark.asyncio
async def test_dispatches_stasis_start_event() -> None:
    received = []

    async def handler(event) -> None:
        received.append(event)

    stream = AsteriskEventStream(
        base_url="http://localhost:8088",
        username="ari",
        password="secret",
        handler=handler,
    )

    await stream.handle_message(
        json.dumps(
            {
                "type": "StasisStart",
                "application": "tccs-core",
                "channel": {
                    "id": "1710000000.1",
                    "name": "PJSIP/1001-00000001",
                    "state": "Up",
                },
            }
        )
    )

    assert len(received) == 1
    assert received[0].event_type == "StasisStart"
    assert received[0].application == "tccs-core"
    assert received[0].channel_id == "1710000000.1"
    assert received[0].channel_name == "PJSIP/1001-00000001"


@pytest.mark.asyncio
async def test_handler_failure_does_not_propagate() -> None:
    async def handler(event) -> None:
        raise RuntimeError("database failure")

    stream = AsteriskEventStream(
        base_url="http://localhost:8088",
        username="ari",
        password="secret",
        handler=handler,
    )

    await stream.handle_message(json.dumps({"type": "StasisStart", "channel": {"id": "1"}}))


@pytest.mark.asyncio
async def test_normalizes_event_without_channel() -> None:
    stream = AsteriskEventStream("http://localhost:8088", "ari", "secret")

    event = AsteriskEventStream.normalize(
        {"type": "ApplicationRegistered", "application": "tccs-core"}
    )

    assert event.event_type == "ApplicationRegistered"
    assert event.application == "tccs-core"
    assert event.channel_id is None
    assert event.channel_name is None


@pytest.mark.asyncio
async def test_handler_is_optional() -> None:
    stream = AsteriskEventStream("http://localhost:8088", "ari", "secret")
    await stream.handle_message(json.dumps({"type": "StasisEnd"}))
