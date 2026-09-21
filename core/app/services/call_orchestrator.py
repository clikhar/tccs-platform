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
        # Always reconcile the controller first. This also handles older calls
        # whose persisted controller channel ID is missing.
        source_channel = await self.asterisk.find_active_channel(
            str(call_id),
            status.source,
            None,
        )
        if source_channel:
            await self.asterisk.attach_participant_channel(
                str(call_id),
                status.source,
                source_channel,
            )

        # Reconcile only the participant requested by this operation.
        #
        # IMPORTANT: a live PJSIP/<extension> channel is NOT sufficient proof
        # that it belongs to this call. The same station may have a channel from
        # another call, and find_active_channel() can only identify an endpoint
        # by name when no persisted channel ID is supplied. For call ownership we
        # therefore trust only the channel ID persisted for THIS participant row.
        #
        # This also fixes the 1001 -> reject -> 1001 retry case: an old channel
        # ID in PostgreSQL must never cause us to return without originating the
        # new participant leg.
        persisted_target_channel = participant[2]
        live_target_channel = None
        if persisted_target_channel:
            live_target_channel = await self.asterisk.find_active_channel(
                str(call_id),
                extension,
                persisted_target_channel,
            )
            await self.service.session.rollback()

        if live_target_channel:
            await self.asterisk.attach_participant_channel(
                str(call_id),
                extension,
                live_target_channel,
            )
            return (await self.service.get(call_id)) or status

        if persisted_target_channel:
            # The DB pointer is stale. Clear it before originating a replacement
            # channel. Never use the stale pointer as an indication that the
            # participant is already connected.
            await self.service.session.rollback()
            async with self.service.session.begin():
                stale_participant = await self.service._participant(call_id, extension)
                stale_participant.asterisk_channel_id = None
                stale_participant.disconnected_at = None

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
        # Every read on AsyncSession can implicitly open a transaction. Close it
        # before invoking mutation methods which own their transactions.
        status = await self.service.get(call_id)
        await self.service.session.rollback()

        if status is None:
            raise ValueError(f"call {call_id} not found")
        if status.conference_id is None or status.state in {"ended", "failed"}:
            raise ValueError(f"call {call_id} is not an active conference")

        participants = await self.service.participants(call_id)
        participant_data = next(
            (
                (item.extension, item.role, item.asterisk_channel_id)
                for item in participants
                if item.extension == extension
            ),
            None,
        )
        await self.service.session.rollback()

        if participant_data is None or participant_data[1] != "participant":
            raise ValueError(f"participant {extension} is not in conference {call_id}")

        persisted_channel = participant_data[2]

        # A participant can leave a persistent conference and later rejoin.
        # Only the persisted channel for this participant proves that a live
        # channel belongs to this conference. Do not adopt an unrelated live
        # PJSIP/<extension> channel from another call.
        if persisted_channel:
            live_channel = await self.asterisk.find_active_channel(
                str(call_id), extension, persisted_channel
            )
            await self.service.session.rollback()
            if live_channel:
                await self.asterisk.attach_participant_channel(
                    str(call_id), extension, live_channel
                )
                return (await self.service.get(call_id)) or status

            await self.service.session.rollback()
            async with self.service.session.begin():
                participant = await self.service._participant(call_id, extension)
                participant.asterisk_channel_id = None
                participant.disconnected_at = None

        # The controller/source leg must also be present and mapped before a
        # participant can be originated. This makes rejoin independent of
        # Core's process-local adapter state.
        source_channel = await self.asterisk.find_active_channel(
            str(call_id), status.source, None
        )
        await self.service.session.rollback()
        if not source_channel:
            raise ValueError(
                f"conference {call_id} has no live controller channel for {status.source}"
            )
        await self.asterisk.attach_participant_channel(
            str(call_id), status.source, source_channel
        )

        channel_id = await self.asterisk.originate_participant(str(call_id), extension)

        # mark_asterisk_leg owns its transaction. The rollback above is
        # essential because the preceding ORM reads may have started one.
        await self.service.session.rollback()
        await self.service.mark_asterisk_leg(
            call_id,
            extension,
            channel_id,
            connected=False,
        )
        # Do not mark the participant connected here. The SIP leg is only
        # connected when Asterisk emits StasisStart for the originated callee.
        # Marking it connected before that event creates a false "mature" call
        # and can race the StasisEnd cleanup path.
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
