from unittest.mock import AsyncMock
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db import Base
from app.models import CallRequest
from app.services.call_orchestrator import CallOrchestrator


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
async def test_create_individual_originates_and_binds_caller_channel(session: AsyncSession) -> None:
    asterisk = AsyncMock()
    asterisk.originate.return_value = "1700000000.10"
    orchestrator = CallOrchestrator(session, asterisk)

    status = await orchestrator.create_individual(CallRequest(source="1001", target="2001"))

    assert UUID(status.call_id)
    assert status.source == "1001"
    assert status.target == "2001"
    asterisk.originate.assert_awaited_once_with("1001", "2001", status.call_id)

    participant = next(
        p for p in await orchestrator.service.participants(UUID(status.call_id))
        if p.extension == "1001"
    )
    assert participant.asterisk_channel_id == "1700000000.10"
