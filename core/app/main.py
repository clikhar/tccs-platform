from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .db import check_database, close_database, get_db_session
from .models import CallRequest, CallStatus, ConferenceCallRequest, ParticipantActionRequest, ParticipantStatus
from .services.call_service import CallService

app = FastAPI(title=settings.app_name, version=settings.app_version)


@app.on_event("shutdown")
async def shutdown() -> None:
    await close_database()


@app.get("/api/v1/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name, "version": settings.app_version}


@app.get("/api/v1/health/live")
async def liveness() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name}


@app.get("/api/v1/health/ready")
async def readiness() -> dict[str, str]:
    if not await check_database():
        raise HTTPException(status_code=503, detail="database unavailable")
    return {"status": "ready", "database": "ok", "service": settings.app_name}


@app.get("/api/v1/system/status")
async def system_status() -> dict[str, str]:
    database = "ok" if await check_database() else "unavailable"
    return {
        "service": settings.app_name,
        "environment": settings.environment,
        "time_utc": datetime.now(timezone.utc).isoformat(),
        "database": database,
    }


@app.post("/api/v1/calls", response_model=CallStatus)
async def create_call(
    request: CallRequest,
    session: AsyncSession = Depends(get_db_session),
) -> CallStatus:
    if not request.source or not request.target:
        raise HTTPException(status_code=400, detail="source and target are required")
    try:
        return await CallService(session).initiate(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/v1/calls/{call_id}", response_model=CallStatus)
async def get_call(
    call_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> CallStatus:
    call_uuid = _parse_call_id(call_id)
    call = await CallService(session).get(call_uuid)
    if call is None:
        raise HTTPException(status_code=404, detail=f"call {call_id} not found")
    return call


@app.post("/api/v1/group-calls", response_model=CallStatus)
async def create_group_call(
    request: ConferenceCallRequest,
    session: AsyncSession = Depends(get_db_session),
) -> CallStatus:
    return await _create_conference(request, "group", session)


@app.post("/api/v1/general-calls", response_model=CallStatus)
async def create_general_call(
    request: ConferenceCallRequest,
    session: AsyncSession = Depends(get_db_session),
) -> CallStatus:
    return await _create_conference(request, "general", session)


@app.get("/api/v1/calls/{call_id}/participants", response_model=list[ParticipantStatus])
async def get_participants(
    call_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> list[ParticipantStatus]:
    call_uuid = _parse_call_id(call_id)
    service = CallService(session)
    if await service.get(call_uuid) is None:
        raise HTTPException(status_code=404, detail=f"call {call_id} not found")
    return [_participant_status(call_uuid, participant) for participant in await service.participants(call_uuid)]


@app.post("/api/v1/calls/{call_id}/participants/{extension}/connect", response_model=ParticipantStatus)
async def connect_participant(
    call_id: str,
    extension: str,
    request: ParticipantActionRequest,
    session: AsyncSession = Depends(get_db_session),
) -> ParticipantStatus:
    return await _participant_action(call_id, extension, request.actor, "connect", session)


@app.post("/api/v1/calls/{call_id}/participants/{extension}/mute", response_model=ParticipantStatus)
async def mute_participant(
    call_id: str,
    extension: str,
    request: ParticipantActionRequest,
    session: AsyncSession = Depends(get_db_session),
) -> ParticipantStatus:
    return await _participant_action(call_id, extension, request.actor, "mute", session)


@app.post("/api/v1/calls/{call_id}/participants/{extension}/unmute", response_model=ParticipantStatus)
async def unmute_participant(
    call_id: str,
    extension: str,
    request: ParticipantActionRequest,
    session: AsyncSession = Depends(get_db_session),
) -> ParticipantStatus:
    return await _participant_action(call_id, extension, request.actor, "unmute", session)


@app.post("/api/v1/calls/{call_id}/participants/{extension}/disconnect", response_model=ParticipantStatus)
async def disconnect_participant(
    call_id: str,
    extension: str,
    request: ParticipantActionRequest,
    session: AsyncSession = Depends(get_db_session),
) -> ParticipantStatus:
    return await _participant_action(call_id, extension, request.actor, "disconnect", session)


def _parse_call_id(call_id: str) -> UUID:
    try:
        return UUID(call_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid call_id") from exc


async def _create_conference(
    request: ConferenceCallRequest,
    mode: str,
    session: AsyncSession,
) -> CallStatus:
    try:
        return await CallService(session).initiate_conference(
            source=request.source,
            targets=request.targets,
            mode=mode,
            conference_id=f"{mode}-{uuid4()}",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def _participant_action(
    call_id: str,
    extension: str,
    actor: str,
    action: str,
    session: AsyncSession,
) -> ParticipantStatus:
    call_uuid = _parse_call_id(call_id)
    service = CallService(session)
    try:
        await service.__getattribute__(f"{action}_participant")(call_uuid, extension, actor)
    except ValueError as exc:
        status = 404 if "not in call" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc

    participant = next(
        (item for item in await service.participants(call_uuid) if item.extension == extension),
        None,
    )
    if participant is None:
        raise HTTPException(status_code=404, detail=f"participant {extension} not found")
    return _participant_status(call_uuid, participant)


def _participant_status(call_id: UUID, participant) -> ParticipantStatus:
    return ParticipantStatus(
        call_id=str(call_id),
        extension=participant.extension,
        role=participant.role,
        muted=participant.muted,
        connected=participant.connected_at is not None and participant.disconnected_at is None,
    )
