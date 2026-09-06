import json
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ..db_models import Call, CallEvent
from ..models import CallRequest, CallState, CallStatus
from ..repositories.calls import CallRepository


class CallService:
    """Owns TCCS call state persistence and call-event creation."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.calls = CallRepository(session)

    async def initiate(self, request: CallRequest) -> CallStatus:
        async with self.session.begin():
            call = Call(
                source_extension=request.source,
                target=request.target,
                mode=request.mode,
                state=CallState.INITIATED,
            )
            await self.calls.add(call)
            self.session.add(
                CallEvent(
                    call_id=call.id,
                    event_type="call.initiated",
                    actor=request.source,
                    payload=json.dumps(
                        {
                            "source": request.source,
                            "target": request.target,
                            "mode": request.mode,
                            "section_id": request.section_id,
                        },
                        sort_keys=True,
                    ),
                )
            )
            await self.session.flush()

        return self._status(call)

    async def get(self, call_id: UUID) -> CallStatus | None:
        call = await self.calls.get(call_id)
        return self._status(call) if call else None

    @staticmethod
    def _status(call: Call) -> CallStatus:
        return CallStatus(
            call_id=str(call.id),
            state=CallState(call.state),
            source=call.source_extension,
            target=call.target,
            conference_id=call.conference_id,
        )
