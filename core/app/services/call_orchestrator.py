from __future__ import annotations

from uuid import UUID

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
        call_id = UUID(status.call_id)
        try:
            channel_id = await self.asterisk.originate(request.source, request.target, status.call_id)
        except Exception as exc:
            await self.service.fail(call_id, actor="asterisk", detail=str(exc))
            raise
        await self.service.bind_asterisk_channel(call_id, request.source, channel_id)
        return (await self.service.get(call_id)) or status

    async def create_conference(
        self,
        source: str,
        targets: list[str],
        mode: str,
        conference_id: str,
    ) -> CallStatus:
        """Create a persistent conference and start its caller leg.

        The ARI Stasis event processor owns participant origination after the
        caller enters Stasis. This keeps one authoritative path for every
        conference participant and avoids originating the first target twice.
        """
        status = await self.service.initiate_conference(
            source=source,
            targets=targets,
            mode=mode,
            conference_id=conference_id,
        )
        call_id = UUID(status.call_id)
        try:
            source_channel = await self.asterisk.originate(source, ",".join(dict.fromkeys(targets)), status.call_id)
            await self.service.bind_asterisk_channel(call_id, source, source_channel)
        except Exception as exc:
            await self.service.fail(call_id, actor="asterisk", detail=str(exc))
            raise
        return (await self.service.get(call_id)) or status
