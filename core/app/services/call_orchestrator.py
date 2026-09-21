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

    async def add_station_to_active_call(
        self,
        call_id: UUID,
        extension: str,
        actor: str | None = None,
    ) -> CallStatus:
        status = await self.service.get(call_id)
        await self.service.session.rollback()
        if status is None or status.state in {"ended", "failed"}:
            raise ValueError(f"call {call_id} is not active")

        if status.conference_id is None:
            await self.service.promote_to_conference(call_id, f"group-{call_id}")
            # promote_to_conference commits its transaction. Do not retain ORM
            # instances across that commit/rollback boundary.
            await self.service.session.rollback()

        participants = await self.service.participants(call_id)
        participant_data = [
            (item.extension, item.role, item.asterisk_channel_id)
            for item in participants
        ]
        await self.service.session.rollback()
        participant = next(
            (item for item in participant_data if item[0] == extension),
            None,
        )

        if participant is None:
            await self.service.add_conference_participant(
                call_id,
                extension,
                actor=actor,
            )
            participants = await self.service.participants(call_id)
            participant_data = [
                (item.extension, item.role, item.asterisk_channel_id)
                for item in participants
            ]
            await self.service.session.rollback()
            participant = next(
                (item for item in participant_data if item[0] == extension),
                None,
            )

        if participant is None:
            raise ValueError(f"participant {extension} is not in call {call_id}")

        # The ARI adapter keeps channel mappings in memory, so a Core restart
        # loses the mapping even though the live Asterisk channels remain.
        # Reconcile persisted channel IDs with live ARI channels before trying
        # to originate another participant.
        all_participants = await self.service.participants(call_id)
        channel_data = [
            (item.extension, item.role, item.asterisk_channel_id)
            for item in all_participants
            if item.asterisk_channel_id
        ]
        await self.service.session.rollback()

        for mapped_extension, role, persisted_channel in channel_data:
            if role not in {"controller", "participant"}:
                continue
            live_channel = await self.asterisk.find_active_channel(
                str(call_id),
                mapped_extension,
                persisted_channel,
            )
            if live_channel:
                await self.asterisk.attach_participant_channel(
                    str(call_id),
                    mapped_extension,
                    live_channel,
                )

        if participant[2]:
            return (await self.service.get(call_id)) or status

        channel_id = await self.asterisk.originate_participant(str(call_id), extension)
        await self.service.mark_asterisk_leg(
            call_id,
            extension,
            channel_id,
            connected=False,
        )
        return (await self.service.get(call_id)) or status

    async def rejoin_conference_participant(
        self,
        call_id: UUID,
        extension: str,
        actor: str | None = None,
    ) -> CallStatus:
        status = await self.service.get(call_id)
        if status is None:
            raise ValueError(f"call {call_id} not found")
        if status.conference_id is None or status.state in {"ended", "failed"}:
            raise ValueError(f"call {call_id} is not an active conference")

        participants = await self.service.participants(call_id)
        participant = next(
            (item for item in participants if item.extension == extension),
            None,
        )
        if participant is None or participant.role != "participant":
            raise ValueError(f"participant {extension} is not in conference {call_id}")
        if participant.asterisk_channel_id:
            return status

        channel_id = await self.asterisk.originate_participant(str(call_id), extension)
        await self.service.mark_asterisk_leg(
            call_id,
            extension,
            channel_id,
            connected=False,
        )
        await self.service.connect_participant(call_id, extension, actor=actor)
        return (await self.service.get(call_id)) or status

    async def create_conference(
        self,
        source: str,
        targets: list[str],
        mode: str,
        conference_id: str,
    ) -> CallStatus:
        """Create a persistent conference and enter the source into Stasis.

        Conference participants are originated from the source Stasis event.
        This keeps ARI bridge operations ordered after each channel has actually
        entered the Stasis application, avoiding a race where addChannel sees a
        freshly originated channel that is not in Stasis yet.
        """
        status = await self.service.initiate_conference(
            source=source,
            targets=targets,
            mode=mode,
            conference_id=conference_id,
        )
        call_id = UUID(status.call_id)
        try:
            source_channel = await self.asterisk.originate(source, targets[0], status.call_id)
            await self.service.bind_asterisk_channel(call_id, source, source_channel)
        except Exception as exc:
            await self.service.fail(call_id, actor="asterisk", detail=str(exc))
            raise
        return (await self.service.get(call_id)) or status
