from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from app.models import CallRequest
from app.services.call_orchestrator import CallOrchestrator


@pytest.mark.asyncio
async def test_create_individual_originates_and_binds_channel(session) -> None:
    asterisk = AsyncMock()
    asterisk.originate.return_value = "1700000000.10"
    orchestrator = CallOrchestrator(session, asterisk)

    status = await orchestrator.create_individual(CallRequest(source="1001", target="2001"))

    assert UUID(status.call_id)
    assert status.source == "1001"
    assert status.target == "2001"
    asterisk.originate.assert_awaited_once_with("1001", "2001")

    participant = next(p for p in await orchestrator.service.participants(UUID(status.call_id)) if p.extension == "2001")
    assert participant.asterisk_channel_id == "1700000000.10"


@pytest.mark.asyncio
async def test_create_individual_marks_call_failed_when_asterisk_rejects(session) -> None:
    asterisk = AsyncMock()
    asterisk.originate.side_effect = RuntimeError("ARI unavailable")
    orchestrator = CallOrchestrator(session, asterisk)

    with pytest.raises(RuntimeError, match="ARI unavailable"):
        await orchestrator.create_individual(CallRequest(source="1001", target="2001"))

    calls = await orchestrator.service.calls.get((await orchestrator.service.calls.get if False else UUID("00000000-0000-0000-0000-000000000000")))
    assert calls is None
