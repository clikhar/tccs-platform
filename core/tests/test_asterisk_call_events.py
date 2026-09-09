import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.adapters.asterisk_events import AsteriskEvent
from app.db import Base
from app.db_models import CallEvent, CallParticipant, CallState
from app.models import CallRequest
from app.services.call_service import CallService


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("postgresql+asyncpg://tccs:tccs@localhost:5432/tccs")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        yield db
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.mark.asyncio
async def test_asterisk_events_drive_persistent_call_state(session: AsyncSession) -> None:
    service = CallService(session)
    status = await service.initiate(CallRequest(source="1001", target="2001"))
    call_id = uuid.UUID(status.call_id)

    started = AsteriskEvent(
        event_type="StasisStart",
        application="tccs-core",
        channel_id="1700000000.1",
        channel_name="PJSIP/2001-00000001",
        payload={
            "type": "StasisStart",
            "application": "tccs-core",
            "args": ["outbound", "1001", "2001"],
            "channel": {"id": "1700000000.1", "name": "PJSIP/2001-00000001"},
        },
    )
    assert await service.handle_asterisk_event(started) is True
    await session.rollback()

    participant = (
        await session.execute(
            select(CallParticipant).where(
                CallParticipant.call_id == call_id,
                CallParticipant.extension == "2001",
            )
        )
    ).scalar_one()
    assert participant.asterisk_channel_id == "1700000000.1"

    connected = AsteriskEvent(
        event_type="ChannelStateChange",
        application="tccs-core",
        channel_id="1700000000.1",
        channel_name="PJSIP/2001-00000001",
        payload={"type": "ChannelStateChange", "channel": {"id": "1700000000.1", "state": "Up"}},
    )
    assert await service.handle_asterisk_event(connected) is True
    await session.rollback()

    current = await service.get(call_id)
    assert current is not None
    assert current.state == CallState.CONNECTED
    await session.rollback()

    ended = AsteriskEvent(
        event_type="StasisEnd",
        application="tccs-core",
        channel_id="1700000000.1",
        channel_name="PJSIP/2001-00000001",
        payload={"type": "StasisEnd", "channel": {"id": "1700000000.1", "name": "PJSIP/2001-00000001"}},
    )
    assert await service.handle_asterisk_event(ended) is True
    await session.rollback()

    current = await service.get(call_id)
    assert current is not None
    assert current.state == CallState.ENDED
    await session.rollback()

    events = await session.execute(select(CallEvent).where(CallEvent.call_id == call_id))
    assert {event.event_type for event in events.scalars()} == {
        "call.initiated",
        "asterisk.channel.started",
        "asterisk.channel.connected",
        "asterisk.channel.ended",
    }


@pytest.mark.asyncio
async def test_unmatched_asterisk_event_is_ignored(session: AsyncSession) -> None:
    service = CallService(session)
    event = AsteriskEvent(
        event_type="ChannelStateChange",
        application="tccs-core",
        channel_id="unknown-channel",
        channel_name="PJSIP/9999-00000099",
        payload={"type": "ChannelStateChange", "channel": {"id": "unknown-channel", "state": "Up"}},
    )

    assert await service.handle_asterisk_event(event) is False
