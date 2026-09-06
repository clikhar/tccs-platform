import json
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db import Base
from app.db_models import CallEvent
from app.models import CallRequest, CallState
from app.services.call_service import CallService


@pytest.fixture
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
async def test_initiate_persists_call_and_event(session: AsyncSession) -> None:
    service = CallService(session)
    request = CallRequest(source="1001", target="2001", section_id="SEC-1")

    status = await service.initiate(request)

    assert uuid.UUID(status.call_id)
    assert status.state == CallState.INITIATED
    assert status.source == "1001"
    assert status.target == "2001"

    loaded = await service.get(uuid.UUID(status.call_id))
    assert loaded == status

    events = await session.execute(
        select(CallEvent).where(CallEvent.call_id == uuid.UUID(status.call_id))
    )
    event = events.scalar_one()
    assert event.event_type == "call.initiated"
    assert event.actor == "1001"
    assert json.loads(event.payload)["section_id"] == "SEC-1"
