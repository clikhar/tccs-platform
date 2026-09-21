import json
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..adapters.asterisk_events import AsteriskEvent
from ..db_models import Call, CallEvent, CallParticipant, CallState
from ..models import CallRequest, CallStatus
from ..repositories.calls import CallRepository


class CallService:
    """Owns TCCS call state and conference participant persistence."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.calls = CallRepository(session)

    async def initiate(self, request: CallRequest) -> CallStatus:
        async with self.session.begin():
            call = Call(source_extension=request.source, target=request.target, mode=request.mode, state=CallState.INITIATED.value)
            await self.calls.add(call)
            self.session.add(CallParticipant(call_id=call.id, extension=request.source, role="controller", muted=False, connected_at=datetime.now(timezone.utc)))
            self.session.add(CallParticipant(call_id=call.id, extension=request.target, role="participant", muted=False))
            self._add_event(call, "call.initiated", request.source, {"source": request.source, "target": request.target, "mode": request.mode, "section_id": request.section_id})
            await self.session.flush()
        return self._status(call)

    async def bind_asterisk_channel(self, call_id: UUID, extension: str, channel_id: str) -> None:
        async with self.session.begin():
            participant = await self._participant(call_id, extension)
            participant.asterisk_channel_id = channel_id
            self._add_event_by_id(call_id, "asterisk.channel.bound", "asterisk", {"channel_id": channel_id, "extension": extension})

    async def mark_asterisk_leg(self, call_id: UUID, extension: str, channel_id: str | None, connected: bool) -> None:
        if not channel_id:
            return

        # SQLAlchemy AsyncSession automatically starts a transaction for reads.
        # This method is also called after ARI reconciliation/origination, where
        # a previous read may have left that implicit transaction open. Mutation
        # methods in CallService own their transaction boundaries, so always close
        # any read transaction before starting the write transaction.
        await self.session.rollback()
        async with self.session.begin():
            participant = await self._participant(call_id, extension)

            # ARI is asynchronous: StasisStart/StasisEnd can be processed
            # before originate_participant() returns. If a fast reject already
            # marked this leg disconnected, a late HTTP response must NEVER
            # resurrect the old channel pointer or the call.
            if not connected and participant.disconnected_at is not None:
                return

            participant.asterisk_channel_id = channel_id
            if connected:
                participant.connected_at = participant.connected_at or datetime.now(timezone.utc)
                call = await self.calls.get(call_id)
                if call is not None:
                    call.state = CallState.CONNECTED.value
            else:
                call = await self.calls.get(call_id)
                if call is not None and call.state == CallState.INITIATED.value:
                    call.state = CallState.RINGING.value
            self._add_event_by_id(call_id, "asterisk.channel.started", "asterisk", {"channel_id": channel_id, "extension": extension, "connected": connected})

    async def fail(self, call_id: UUID, actor: str, detail: str) -> None:
        async with self.session.begin():
            call = await self.calls.get(call_id)
            if call is None:
                return
            call.state = CallState.FAILED.value
            self._add_event_by_id(call_id, "call.failed", actor, {"detail": detail})

    async def initiate_conference(self, source: str, targets: list[str], mode: str, conference_id: str) -> CallStatus:
        unique_targets = list(dict.fromkeys(targets))
        if not unique_targets:
            raise ValueError("conference requires at least one target")
        async with self.session.begin():
            call = Call(source_extension=source, target=",".join(unique_targets), mode=mode, state=CallState.CONFERENCE.value, conference_id=conference_id)
            await self.calls.add(call)
            self.session.add(CallParticipant(call_id=call.id, extension=source, role="controller", muted=False, connected_at=datetime.now(timezone.utc)))
            for extension in unique_targets:
                self.session.add(CallParticipant(call_id=call.id, extension=extension, role="participant", muted=False))
            self._add_event(call, "conference.created", source, {"mode": mode, "conference_id": conference_id, "targets": unique_targets})
            await self.session.flush()
        return self._status(call)

    async def active_call_for_source(self, source: str) -> CallStatus | None:
        result = await self.session.execute(
            select(Call)
            .where(
                Call.source_extension == source,
                Call.state.notin_([CallState.ENDED.value, CallState.FAILED.value]),
            )
            .order_by(Call.started_at.desc())
            .limit(1)
        )
        call = result.scalar_one_or_none()
        return self._status(call) if call else None

    async def promote_to_conference(self, call_id: UUID, conference_id: str) -> None:
        async with self.session.begin():
            call = await self.calls.get(call_id)
            if call is None or call.state in {CallState.ENDED.value, CallState.FAILED.value}:
                raise ValueError(f"call {call_id} is not active")
            call.mode = "group"
            call.conference_id = conference_id
            self._add_event_by_id(
                call_id,
                "conference.promoted",
                call.source_extension,
                {"conference_id": conference_id},
            )

    async def active_conference_for_source(self, source: str) -> CallStatus | None:
        result = await self.session.execute(
            select(Call)
            .where(
                Call.source_extension == source,
                Call.conference_id.is_not(None),
                Call.state.in_([
                    CallState.INITIATED.value,
                    CallState.RINGING.value,
                    CallState.CONNECTED.value,
                    CallState.CONFERENCE.value,
                ]),
            )
            .order_by(Call.started_at.desc())
            .limit(1)
        )
        call = result.scalar_one_or_none()
        return self._status(call) if call else None

    async def add_conference_participant(
        self,
        call_id: UUID,
        extension: str,
        actor: str | None = None,
    ) -> None:
        async with self.session.begin():
            call = await self.calls.get(call_id)
            if call is None or call.conference_id is None or call.state in {
                CallState.ENDED.value,
                CallState.FAILED.value,
            }:
                raise ValueError(f"call {call_id} is not an active conference")

            result = await self.session.execute(
                select(CallParticipant).where(
                    CallParticipant.call_id == call_id,
                    CallParticipant.extension == extension,
                )
            )
            participant = result.scalar_one_or_none()
            if participant is None:
                participant = CallParticipant(
                    call_id=call_id,
                    extension=extension,
                    role="participant",
                    muted=False,
                )
                self.session.add(participant)
                targets = [
                    value.strip()
                    for value in (call.target or "").split(",")
                    if value.strip()
                ]
                if extension not in targets:
                    targets.append(extension)
                    call.target = ",".join(targets)
            participant.disconnected_at = None
            # This method is also used while attaching an inbound leg. The
            # channel is not connected until ARI StasisStart is processed, so
            # do not manufacture connected_at here.
            self._add_event_by_id(
                call_id,
                "participant.connecting",
                actor or extension,
                {"extension": extension, "inbound": True},
            )

    async def connect_participant(self, call_id: UUID, extension: str, actor: str | None = None) -> None:
        async with self.session.begin():
            participant = await self._participant(call_id, extension)
            if participant.role == "controller":
                raise ValueError("controller cannot be reconnected as a conference participant")
            participant.disconnected_at = None
            participant.connected_at = participant.connected_at or datetime.now(timezone.utc)
            self._add_event_by_id(
                call_id,
                "participant.connected",
                actor or extension,
                {"extension": extension, "rejoined": True},
            )

    async def mute_participant(self, call_id: UUID, extension: str, actor: str | None = None) -> None:
        async with self.session.begin():
            participant = await self._participant(call_id, extension)
            participant.muted = True
            self._add_event_by_id(call_id, "participant.muted", actor or extension, {"extension": extension})

    async def unmute_participant(self, call_id: UUID, extension: str, actor: str | None = None) -> None:
        async with self.session.begin():
            participant = await self._participant(call_id, extension)
            participant.muted = False
            self._add_event_by_id(call_id, "participant.unmuted", actor or extension, {"extension": extension})

    async def remove_participant(self, call_id: UUID, extension: str, actor: str | None = None) -> None:
        async with self.session.begin():
            participant = await self._participant(call_id, extension)
            if participant.role == "controller":
                raise ValueError("controller cannot be removed from persistent conference")
            participant.disconnected_at = datetime.now(timezone.utc)
            self._add_event_by_id(call_id, "participant.disconnected", actor or extension, {"extension": extension})

    async def handle_asterisk_event(self, event: AsteriskEvent) -> bool:
        if not event.channel_id:
            return False
        handled = await self._handle_asterisk_event_in_transaction(event)
        if handled:
            await self.session.commit()
        return handled

    async def _handle_asterisk_event_in_transaction(self, event: AsteriskEvent) -> bool:
        if event.event_type == "StasisStart":
            return await self._handle_stasis_start(event)
        participant = await self._participant_by_channel(event.channel_id)
        if participant is None:
            return False
        call = await self.calls.get(participant.call_id)
        if call is None:
            return False
        if event.event_type == "ChannelStateChange":
            state = str((event.payload.get("channel") or {}).get("state", "")).lower()
            if state in {"ring", "ringing"}:
                call.state = CallState.RINGING.value
                self._add_event_by_id(call.id, "asterisk.channel.ringing", event.channel_name or event.channel_id, {"channel_id": event.channel_id})
            elif state in {"up", "connected"}:
                participant.connected_at = participant.connected_at or datetime.now(timezone.utc)
                call.state = CallState.CONNECTED.value
                self._add_event_by_id(call.id, "asterisk.channel.connected", event.channel_name or event.channel_id, {"channel_id": event.channel_id})
            return True
        if event.event_type == "StasisEnd":
            participant.disconnected_at = participant.disconnected_at or datetime.now(timezone.utc)
            participant.asterisk_channel_id = None

            # An individual call is a two-party session. If the station leg ends,
            # the controller leg must also be considered ended so the controller
            # can immediately originate the next call. Previously the controller
            # participant remained connected in the database, leaving the call in
            # CONNECTED state and making the browser/Asterisk controller endpoint
            # appear permanently IN CALL.
            if (
                call.mode == "individual"
                and participant.role == "participant"
            ):
                call.state = CallState.ENDED.value
                call.ended_at = datetime.now(timezone.utc)
            else:
                # Conference lifetime is determined by participant legs, not the
                # controller leg. The controller is intentionally persistent while
                # at least one station remains in the conference.
                active = await self._active_participants(call.id, role="participant")
                if not active:
                    call.state = CallState.ENDED.value
                    call.ended_at = datetime.now(timezone.utc)

            self._add_event_by_id(
                call.id,
                "asterisk.channel.ended",
                event.channel_name or event.channel_id,
                {"channel_id": event.channel_id, "extension": participant.extension},
            )
            return True
        return False

    async def get(self, call_id: UUID) -> CallStatus | None:
        call = await self.calls.get(call_id)
        return self._status(call) if call else None

    async def participants(self, call_id: UUID) -> list[CallParticipant]:
        result = await self.session.execute(select(CallParticipant).where(CallParticipant.call_id == call_id).order_by(CallParticipant.extension))
        return list(result.scalars())

    async def participants_for_channel(self, channel_id: str) -> list[CallParticipant]:
        result = await self.session.execute(select(CallParticipant).where(CallParticipant.asterisk_channel_id == channel_id))
        return list(result.scalars())

    async def _handle_stasis_start(self, event: AsteriskEvent) -> bool:
        args = event.payload.get("args") or []
        if len(args) < 3 or args[0] != "outbound":
            return False
        source, target = str(args[1]), str(args[2])
        result = await self.session.execute(select(Call).where(Call.source_extension == source, Call.target == target, Call.state.in_([CallState.INITIATED.value, CallState.RINGING.value])).order_by(Call.started_at.desc()).limit(1))
        call = result.scalar_one_or_none()
        if call is None:
            return False
        participant = await self._participant(call.id, target)
        participant.asterisk_channel_id = event.channel_id
        call.state = CallState.RINGING.value
        self._add_event_by_id(call.id, "asterisk.channel.started", event.channel_name or event.channel_id, {"channel_id": event.channel_id, "source": source, "target": target})
        return True

    async def _participant_by_channel(self, channel_id: str) -> CallParticipant | None:
        result = await self.session.execute(select(CallParticipant).where(CallParticipant.asterisk_channel_id == channel_id))
        return result.scalar_one_or_none()

    async def _active_participants(
        self,
        call_id: UUID,
        role: str | None = None,
    ) -> list[CallParticipant]:
        query = select(CallParticipant).where(
            CallParticipant.call_id == call_id,
            CallParticipant.disconnected_at.is_(None),
            CallParticipant.asterisk_channel_id.is_not(None),
        )
        if role is not None:
            query = query.where(CallParticipant.role == role)
        result = await self.session.execute(query)
        return list(result.scalars())

    async def _participant(self, call_id: UUID, extension: str) -> CallParticipant:
        result = await self.session.execute(select(CallParticipant).where(CallParticipant.call_id == call_id, CallParticipant.extension == extension))
        participant = result.scalar_one_or_none()
        if participant is None:
            raise ValueError(f"participant {extension} is not in call {call_id}")
        return participant

    def _add_event(self, call: Call, event_type: str, actor: str, payload: dict) -> None:
        self.session.add(CallEvent(call_id=call.id, event_type=event_type, actor=actor, payload=json.dumps(payload, sort_keys=True)))

    def _add_event_by_id(self, call_id: UUID, event_type: str, actor: str, payload: dict) -> None:
        self.session.add(CallEvent(call_id=call_id, event_type=event_type, actor=actor, payload=json.dumps(payload, sort_keys=True)))

    @staticmethod
    def _status(call: Call) -> CallStatus:
        return CallStatus(call_id=str(call.id), state=CallState(call.state), source=call.source_extension, target=call.target, conference_id=call.conference_id)
