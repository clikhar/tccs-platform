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
                await session.rollback()
                await service.mark_asterisk_leg(call_id, status.source, event.channel_id, connected=True)

                # Conferences persist their targets as a comma-separated value.
                # Originate each endpoint separately; never pass the entire list
                # to Asterisk as one PJSIP endpoint.
                targets = [target.strip() for target in (status.target or str(args[2])).split(",") if target.strip()]
                for target in dict.fromkeys(targets):
                    await self.asterisk.originate_participant(call_id, target)
                return

            if event.event_type == "StasisStart" and len(args) >= 3 and args[0] == "inbound":
                controller = str(args[1]).strip()
                participant = str(args[2]).strip()
                if not controller or not participant or event.channel_id is None:
                    return

                status = await service.active_conference_for_source(controller)
                if status is None or status.conference_id is None:
                    # No active conference exists for this controller. Do not
                    # deliver the call to the browser as a second SIP session.
                    try:
                        await self.asterisk.remove_channel(event.channel_id)
                    except Exception:
                        pass
                    return

                await session.rollback()
                try:
                    await service.add_conference_participant(
                        UUID(status.call_id),
                        participant,
                        actor=participant,
                    )
                    await session.rollback()
                    await self.asterisk.attach_participant_channel(
                        status.call_id,
                        participant,
                        event.channel_id,
                    )
                    await self.asterisk.answer_channel(event.channel_id)
                    await service.mark_asterisk_leg(
                        UUID(status.call_id),
                        participant,
                        event.channel_id,
                        connected=True,
                    )
                    await self.asterisk.bridge_call(status.call_id, participant)
                except Exception:
                    try:
                        await self.asterisk.remove_channel(event.channel_id)
                    except Exception:
                        pass
                    raise
                return

            if event.event_type == "StasisStart" and len(args) >= 3 and args[0] == "callee":
                call_id = UUID(str(args[1]))
                participant = str(args[2])
                await session.rollback()
                await service.mark_asterisk_leg(call_id, participant, event.channel_id, connected=True)
                # Only bridge the source and this participant. Other conference
                # legs may have been originated but have not entered Stasis yet.
                await self.asterisk.bridge_call(call_id, participant)
                return

            call_id: UUID | None = None
            if event.event_type == "StasisEnd" and event.channel_id:
                participants = await service.participants_for_channel(event.channel_id)
                if participants:
                    call_id = participants[0].call_id

            await service.handle_asterisk_event(event)

            if call_id is not None:
                # For an individual call, the station hanging up must terminate
                # the controller leg as well. Otherwise the browser keeps its
                # SIP.js session established and Asterisk reports the controller
                # as IN CALL, so the next call arrives as a second INVITE and is
                # rejected by the existing controller session.
                status = await service.get(call_id)
                if status is not None and status.state.value == "ENDED":
                    for participant in await service.participants(call_id):
                        if (
                            participant.role == "controller"
                            and participant.asterisk_channel_id
                        ):
                            try:
                                await self.asterisk.remove_channel(
                                    participant.asterisk_channel_id
                                )
                            except Exception:
                                # cleanup_call below still performs best-effort
                                # bridge/channel cleanup using its live mapping.
                                pass

                await self.asterisk.cleanup_call(str(call_id), event.channel_id or "")
