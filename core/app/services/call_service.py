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
            call = Call(
                source_extension=request.source,
                target=request.target,
                mode=request.mode,
                state=CallState.INITIATED.value,
            )
            await self.calls.add(call)
            self.session.add(
                CallParticipant(
                    call_id=call.id,
                    extension=request.source,
                    role="controller",
                    muted=False,
                    connected_at=datetime.now(timezone.utc),
                )
            )
            self.session.add(
                CallParticipant(
                    call_id=call.id,
                    extension=request.target,
                    role="participant",
                    muted=False,
                )
            )
            self._add_event(
                call,
                "call.initiated",
                request.source,
                {
                    "source": request.source,
                    "target": request.target,
                    "mode": request.mode,
                    "section_id": request.section_id,
                },
            )
            await self.session.flush()

        return self._status(call)

    async def initiate_conference(
        self,
        source: str,
        targets: list[str],
        mode: str,
        conference_id: str,
    ) -> CallStatus:
        """Create a persistent group/general conference with muted participants."""
        unique_targets = list(dict.fromkeys(targets))
        if not unique_targets:
            raise ValueError("conference requires at least one target")

        async with self.session.begin():
            call = Call(
                source_extension=source,
                target=",".join(unique_targets),
                mode=mode,
                state=CallState.CONFERENCE.value,
                conference_id=conference_id,
            )
            await self.calls.add(call)
            self.session.add(
                CallParticipant(
                    call_id=call.id,
                    extension=source,
                    role="controller",
                    muted=False,
                    connected_at=datetime.now(timezone.utc),
                )
            )
            for extension in unique_targets:
                self.session.add(
                    CallParticipant(
                        call_id=call.id,
                        extension=extension,
                        role="participant",
                        muted=True,
                    )
                )
            self._add_event(
                call,
                "conference.created",
                source,
                {
                    "mode": mode,
                    "conference_id": conference_id,
                    "targets": unique_targets,
                },
            )
            await self.session.flush()

        return self._status(call)

    async def connect_participant(self, call_id: UUID, extension: str, actor: str | None = None) -> None:
        async with self.session.begin():
            participant = await self._participant(call_id, extension)
            participant.connected_at = participant.connected_at or datetime.now(timezone.utc)
            self._add_event_by_id(call_id, "participant.connected", actor or extension, {"extension": extension})

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
        """Apply an ARI channel event to persistent TCCS call state.

        The method participates in an existing session transaction when the
        caller already has one; otherwise it owns and commits its transaction.
        This avoids attempting a second outer transaction after a caller has
        performed a read (SQLAlchemy sessions use autobegin for such reads).
        """
        if not event.channel_id:
            return False

        if self.session.in_transaction():
            return await self._handle_asterisk_event_in_transaction(event)

        async with self.session.begin():
            return await self._handle_asterisk_event_in_transaction(event)

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
                self._add_event_by_id(
                    call.id,
                    "asterisk.channel.ringing",
                    event.channel_name or event.channel_id,
                    {"channel_id": event.channel_id},
                )
            elif state in {"up", "connected"}:
                participant.connected_at = participant.connected_at or datetime.now(timezone.utc)
                call.state = CallState.CONNECTED.value
                self._add_event_by_id(
                    call.id,
                    "asterisk.channel.connected",
                    event.channel_name or event.channel_id,
                    {"channel_id": event.channel_id},
                )
            return True

        if event.event_type == "StasisEnd":
            participant.disconnected_at = participant.disconnected_at or datetime.now(timezone.utc)
            participant.asterisk_channel_id = None
            active = await self._active_participants(call.id)
            if not active:
                call.state = CallState.ENDED.value
                call.ended_at = datetime.now(timezone.utc)
            self._add_event_by_id(
                call.id,
                "asterisk.channel.ended",
                event.channel_name or event.channel_id,
                {"channel_id": event.channel_id},
            )
            return True

        return False

    async def get(self, call_id: UUID) -> CallStatus | None:
        call = await self.calls.get(call_id)
        return self._status(call) if call else None

    async def participants(self, call_id: UUID) -> list[CallParticipant]:
        result = await self.session.execute(
            select(CallParticipant)
            .where(CallParticipant.call_id == call_id)
            .order_by(CallParticipant.extension)
        )
        return list(result.scalars())

    async def _handle_stasis_start(self, event: AsteriskEvent) -> bool:
        args = event.payload.get("args") or []
        if len(args) < 3 or args[0] != "outbound":
            return False

        source, target = str(args[1]), str(args[2])
        result = await self.session.execute(
            select(Call)
            .where(
                Call.source_extension == source,
                Call.target == target,
                Call.state.in_([CallState.INITIATED.value, CallState.RINGING.value]),
            )
            .order_by(Call.started_at.desc())
            .limit(1)
        )
        call = result.scalar_one_or_none()
        if call is None:
            return False

        participant = await self._participant(call.id, target)
        participant.asterisk_channel_id = event.channel_id
        call.state = CallState.RINGING.value
        self._add_event_by_id(
            call.id,
            "asterisk.channel.started",
            event.channel_name or event.channel_id,
            {"channel_id": event.channel_id, "source": source, "target": target},
        )
        return True

    async def _participant_by_channel(self, channel_id: str) -> CallParticipant | None:
        result = await self.session.execute(
            select(CallParticipant).where(CallParticipant.asterisk_channel_id == channel_id)
        )
        return result.scalar_one_or_none()

    async def _active_participants(self, call_id: UUID) -> list[CallParticipant]:
        result = await self.session.execute(
            select(CallParticipant).where(
                CallParticipant.call_id == call_id,
                CallParticipant.disconnected_at.is_(None),
                CallParticipant.asterisk_channel_id.is_not(None),
            )
        )
        return list(result.scalars())

    async def _participant(self, call_id: UUID, extension: str) -> CallParticipant:
        result = await self.session.execute(
            select(CallParticipant).where(
                CallParticipant.call_id == call_id,
                CallParticipant.extension == extension,
            )
        )
        participant = result.scalar_one_or_none()
        if participant is None:
            raise ValueError(f"participant {extension} is not in call {call_id}")
        return participant

    def _add_event(self, call: Call, event_type: str, actor: str, payload: dict) -> None:
        self.session.add(
            CallEvent(
                call_id=call.id,
                event_type=event_type,
                actor=actor,
                payload=json.dumps(payload, sort_keys=True),
            )
        )

    def _add_event_by_id(self, call_id: UUID, event_type: str, actor: str, payload: dict) -> None:
        self.session.add(
            CallEvent(
                call_id=call_id,
                event_type=event_type,
                actor=actor,
                payload=json.dumps(payload, sort_keys=True),
            )
        )

    @staticmethod
    def _status(call: Call) -> CallStatus:
        return CallStatus(
            call_id=str(call.id),
            state=CallState(call.state),
            source=call.source_extension,
            target=call.target,
            conference_id=call.conference_id,
        )
