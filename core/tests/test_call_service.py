import json
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db import Base
from app.db_models import CallEvent
from app.models import CallRequest, CallState
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
async def test_initiate_persists_call_event_and_participants(session: AsyncSession) -> None:
    service = CallService(session)
    request = CallRequest(source="1001", target="2001", section_id="SEC-1")

    status = await service.initiate(request)

    call_id = uuid.UUID(status.call_id)
    assert status.state == CallState.INITIATED
    assert status.source == "1001"
    assert status.target == "2001"

    loaded = await service.get(call_id)
    assert loaded == status
    await session.rollback()

    events = await session.execute(select(CallEvent).where(CallEvent.call_id == call_id))
    event = events.scalar_one()
    assert event.event_type == "call.initiated"
    assert event.actor == "1001"
    assert json.loads(event.payload)["section_id"] == "SEC-1"

    participants = await service.participants(call_id)
    assert [(p.extension, p.role, p.muted) for p in participants] == [
        ("1001", "controller", False),
        ("2001", "participant", False),
    ]
    await session.rollback()


@pytest.mark.asyncio
async def test_conference_persists_muted_participants_and_state(session: AsyncSession) -> None:
    service = CallService(session)

    status = await service.initiate_conference(
        source="1001",
        targets=["2001", "2002", "2001"],
        mode="general",
        conference_id="SEC-01-GENERAL",
    )

    call_id = uuid.UUID(status.call_id)
    assert status.state == CallState.CONFERENCE
    assert status.conference_id == "SEC-01-GENERAL"
    assert status.target == "2001,2002"

    participants = await service.participants(call_id)
    assert [(p.extension, p.role, p.muted) for p in participants] == [
        ("1001", "controller", False),
        ("2001", "participant", True),
        ("2002", "participant", True),
    ]
    await session.rollback()

    await service.unmute_participant(call_id, "2001", actor="1001")
    await service.connect_participant(call_id, "2001", actor="2001")
    await service.remove_participant(call_id, "2002", actor="1001")

    participants = await service.participants(call_id)
    by_extension = {p.extension: p for p in participants}
    assert by_extension["2001"].muted is False
    assert by_extension["2001"].connected_at is not None
    assert by_extension["2002"].disconnected_at is not None
    await session.rollback()

    events = await session.execute(
        select(CallEvent).where(CallEvent.call_id == call_id).order_by(CallEvent.occurred_at)
    )
    event_types = [event.event_type for event in events.scalars()]
    assert set(event_types) == {
        "conference.created",
        "participant.unmuted",
        "participant.connected",
        "participant.disconnected",
    }
    assert len(event_types) == 4
