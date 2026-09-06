import json
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
