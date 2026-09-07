from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from ..adapters.asterisk import AsteriskClient
from ..models import CallRequest, CallStatus
from .call_service import CallService


class CallOrchestrator:
    """Coordinates persistent call creation with the Asterisk adapter."""

    def __init__(self, session: AsyncSession, asterisk: AsteriskClient) -> None:
        self.service = CallService(session)
        self.asterisk = asterisk

    async def create_individual(self, request: CallRequest) -> CallStatus:
        status = await self.service.initiate(request)
        from uuid import UUID

        call_id = UUID(status.call_id)
        try:
            channel_id = await self.asterisk.originate(request.source, request.target, status.call_id)
        except Exception as exc:
            await self.service.fail(call_id, actor="asterisk", detail=str(exc))
            raise
        await self.service.bind_asterisk_channel(call_id, request.source, channel_id)
        return (await self.service.get(call_id)) or status
