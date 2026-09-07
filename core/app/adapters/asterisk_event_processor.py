from __future__ import annotations

from uuid import UUID

from .asterisk import AsteriskClient
from .asterisk_events import AsteriskEvent
from ..db import AsyncSessionLocal
from ..services.call_service import CallService


class AsteriskEventProcessor:
    """Bridge ARI events into persistent TCCS call control."""

    def __init__(self, asterisk: AsteriskClient) -> None:
        self.asterisk = asterisk

    async def __call__(self, event: AsteriskEvent) -> None:
        async with AsyncSessionLocal() as session:
            service = CallService(session)
            args = event.payload.get("args") or []

            if event.event_type == "StasisStart" and len(args) >= 3 and args[0] == "source":
                call_id = UUID(str(args[1]))
                status = await service.get(call_id)
                if status is None or status.source is None:
                    return
                await service.mark_asterisk_leg(call_id, status.source, event.channel_id, connected=True)
                await self.asterisk.originate_participant(call_id, status.target or str(args[2]))
                return

            if event.event_type == "StasisStart" and len(args) >= 3 and args[0] == "callee":
                call_id = UUID(str(args[1]))
                status = await service.get(call_id)
                if status is None or status.target is None:
                    return
                await service.mark_asterisk_leg(call_id, status.target, event.channel_id, connected=True)
                await self.asterisk.bridge_call(call_id)
                return

            call_id: UUID | None = None
            if event.event_type == "StasisEnd" and event.channel_id:
                participants = await service.participants_for_channel(event.channel_id)
                if participants:
                    call_id = participants[0].call_id

            await service.handle_asterisk_event(event)

            if call_id is not None:
                await self.asterisk.cleanup_call(str(call_id), event.channel_id or "")
