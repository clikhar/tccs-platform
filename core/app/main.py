import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .adapters.asterisk import AsteriskAdapterError, AsteriskHttpClient
from .adapters.asterisk_event_processor import AsteriskEventProcessor
from .adapters.asterisk_events import AsteriskEventStream
from .config import settings
from .db import check_database, close_database, get_db_session
from .db_models import Call, CallParticipant, CallState
from .models import CallRequest, CallStatus, ConferenceCallRequest, ParticipantActionRequest, ParticipantStatus
from .services.call_orchestrator import CallOrchestrator
from .services.call_service import CallService


_asterisk_client: AsteriskHttpClient | None = None
_asterisk_event_stream: AsteriskEventStream | None = None
_asterisk_event_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _asterisk_client, _asterisk_event_stream, _asterisk_event_task
    if settings.asterisk_ari_enabled:
        _asterisk_client = AsteriskHttpClient(
            base_url=settings.asterisk_ari_url,
            username=settings.asterisk_ari_username,
            password=settings.asterisk_ari_password,
            app=settings.asterisk_ari_app,
            timeout=settings.asterisk_ari_timeout_seconds,
        )
        _asterisk_event_stream = AsteriskEventStream(
            base_url=settings.asterisk_ari_url,
            username=settings.asterisk_ari_username,
            password=settings.asterisk_ari_password,
            app=settings.asterisk_ari_app,
            handler=AsteriskEventProcessor(_asterisk_client),
            reconnect_delay=settings.asterisk_ari_reconnect_delay,
        )
        _asterisk_event_task = asyncio.create_task(_asterisk_event_stream.run_forever())
    try:
        yield
    finally:
        if _asterisk_event_stream is not None:
            await _asterisk_event_stream.stop()
        if _asterisk_event_task is not None:
            try:
                await asyncio.wait_for(_asterisk_event_task, timeout=3.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                _asterisk_event_task.cancel()
        if _asterisk_client is not None:
            await _asterisk_client.aclose()
        _asterisk_event_stream = None
        _asterisk_event_task = None
        _asterisk_client = None
        await close_database()


app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)


@app.get("/api/v1/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name, "version": settings.app_version}


@app.get("/api/v1/health/live")
async def health_live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/health/ready")
async def health_ready() -> dict[str, str]:
    if not await check_database():
        raise HTTPException(status_code=503, detail="database unavailable")
    return {"status": "ready"}


@app.get("/api/v1/system/status")
async def system_status() -> dict[str, str]:
    database = "ok" if await check_database() else "unavailable"
    return {"service": settings.app_name, "environment": settings.environment, "time_utc": datetime.now(timezone.utc).isoformat(), "database": database}


@app.post("/api/v1/calls", response_model=CallStatus)
async def create_call(request: CallRequest, session: AsyncSession = Depends(get_db_session)) -> CallStatus:
    try:
        if settings.asterisk_ari_enabled:
            if _asterisk_client is None:
                raise HTTPException(status_code=503, detail="Asterisk adapter unavailable")
            return await CallOrchestrator(session, _asterisk_client).create_individual(request)
        return await CallService(session).initiate(request)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Asterisk call setup failed: {exc}") from exc


@app.get("/api/v1/active-calls/participant/{extension}")
async def active_call_for_participant(extension: str, session: AsyncSession = Depends(get_db_session)) -> dict:
    value = str(extension).strip()
    result = await session.execute(
        select(CallParticipant)
        .join(Call, Call.id == CallParticipant.call_id)
        .where(
            CallParticipant.extension == value,
            CallParticipant.role == "participant",
            CallParticipant.disconnected_at.is_(None),
            # A participant is actionable only after ARI has persisted the
            # actual Asterisk channel identity. Conference rows are created
            # before their target legs enter Stasis, so unstarted participants
            # must never be treated as an active call.
            CallParticipant.asterisk_channel_id.is_not(None),
            Call.state.notin_(["ended", "failed"]),
        )
        .order_by(Call.started_at.desc())
    )
    participants = result.scalars().all()
    if not participants:
        raise HTTPException(status_code=404, detail=f"no active call for participant {value}")

    # DB channel IDs can become stale if Core misses a StasisEnd or is
    # restarted while Asterisk remains in service. Only advertise a call to
    # Controller when the persisted channel is still live in ARI.
    if _asterisk_client is None:
        raise HTTPException(status_code=503, detail="Asterisk adapter unavailable")

    for participant in participants:
        channel_id = participant.asterisk_channel_id
        live_channel = None
        if channel_id:
            live_channel = await _asterisk_client.find_active_channel(
                str(participant.call_id), value, channel_id
            )
        # If the persisted channel is missing/stale, reconcile against the live
        # endpoint. This covers a missed StasisStart or a Core restart while the
        # subscriber leg remains established in Asterisk.
        if not live_channel:
            live_channel = await _asterisk_client.find_active_channel(
                str(participant.call_id), value
            )
        if live_channel:
            if live_channel != channel_id:
                await session.rollback()
                async with session.begin():
                    refreshed = await session.get(CallParticipant, participant.id)
                    if refreshed is not None and refreshed.disconnected_at is None:
                        refreshed.asterisk_channel_id = live_channel
            return {"call_id": str(participant.call_id)}

    raise HTTPException(status_code=404, detail=f"no live Asterisk call for participant {value}")

@app.get("/api/v1/active-conferences/source/{extension}")
async def active_conference_for_source(extension: str, session: AsyncSession = Depends(get_db_session)) -> dict:
    value = str(extension).strip()
    result = await session.execute(
        select(Call)
        .where(
            Call.source_extension == value,
            Call.conference_id.is_not(None),
            Call.state.notin_([CallState.ENDED.value, CallState.FAILED.value]),
        )
        .order_by(Call.started_at.desc())
        .limit(1)
    )
    call = result.scalar_one_or_none()
    if call is None:
        raise HTTPException(status_code=404, detail=f"no active conference for source {value}")
    return {"call_id": str(call.id), "conference_id": str(call.conference_id)}

@app.get("/api/v1/calls/{call_id}", response_model=CallStatus)
async def get_call(call_id: str, session: AsyncSession = Depends(get_db_session)) -> CallStatus:
    call_uuid = _parse_call_id(call_id)
    call = await CallService(session).get(call_uuid)
    if call is None:
        raise HTTPException(status_code=404, detail=f"call {call_id} not found")
    return call


@app.post("/api/v1/group-calls", response_model=CallStatus)
async def create_group_call(request: ConferenceCallRequest, session: AsyncSession = Depends(get_db_session)) -> CallStatus:
    return await _create_conference(request, "group", session)


@app.post("/api/v1/general-calls", response_model=CallStatus)
async def create_general_call(request: ConferenceCallRequest, session: AsyncSession = Depends(get_db_session)) -> CallStatus:
    return await _create_conference(request, "general", session)


@app.get("/api/v1/calls/{call_id}/participants", response_model=list[ParticipantStatus])
async def get_participants(call_id: str, session: AsyncSession = Depends(get_db_session)) -> list[ParticipantStatus]:
    call_uuid = _parse_call_id(call_id)
    service = CallService(session)
    if await service.get(call_uuid) is None:
        raise HTTPException(status_code=404, detail=f"call {call_id} not found")
    return [_participant_status(call_uuid, item) for item in await service.participants(call_uuid)]


@app.post("/api/v1/calls/{call_id}/participants/{extension}/connect", response_model=ParticipantStatus)
async def connect_participant(call_id: str, extension: str, request: ParticipantActionRequest, session: AsyncSession = Depends(get_db_session)) -> ParticipantStatus:
    call_uuid = _parse_call_id(call_id)
    try:
        if settings.asterisk_ari_enabled:
            if _asterisk_client is None:
                raise HTTPException(status_code=503, detail="Asterisk adapter unavailable")
            service = CallService(session)
            status = await service.get(call_uuid)
            if status is None:
                raise HTTPException(status_code=404, detail=f"call {call_id} not found")
            if status.conference_id is None:
                raise HTTPException(status_code=409, detail="participant rejoin is only supported for conferences")
            await session.rollback()
            await CallOrchestrator(session, _asterisk_client).rejoin_conference_participant(
                call_uuid,
                str(extension).strip(),
                actor=request.actor,
            )
            participant = next(
                item
                for item in await service.participants(call_uuid)
                if item.extension == str(extension).strip()
            )
            return _participant_status(call_uuid, participant)
        return await _participant_action(call_id, extension, request.actor, "connect", session)
    except HTTPException:
        raise
    except (AsteriskAdapterError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/v1/calls/{call_id}/participants/{extension}/mute", response_model=ParticipantStatus)
async def mute_participant(call_id: str, extension: str, request: ParticipantActionRequest, session: AsyncSession = Depends(get_db_session)) -> ParticipantStatus:
    return await _participant_action(call_id, extension, request.actor, "mute", session)


@app.post("/api/v1/calls/{call_id}/participants/{extension}/unmute", response_model=ParticipantStatus)
async def unmute_participant(call_id: str, extension: str, request: ParticipantActionRequest, session: AsyncSession = Depends(get_db_session)) -> ParticipantStatus:
    return await _participant_action(call_id, extension, request.actor, "unmute", session)


@app.post("/api/v1/calls/{call_id}/participants/{extension}/disconnect", response_model=ParticipantStatus)
async def disconnect_participant(call_id: str, extension: str, request: ParticipantActionRequest, session: AsyncSession = Depends(get_db_session)) -> ParticipantStatus:
    return await _participant_action(call_id, extension, request.actor, "remove", session)


def _parse_call_id(call_id: str) -> UUID:
    try:
        return UUID(call_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid call_id") from exc


async def _create_conference(request: ConferenceCallRequest, mode: str, session: AsyncSession) -> CallStatus:
    conference_id = f"{mode}-{uuid4()}"
    try:
        if settings.asterisk_ari_enabled:
            if _asterisk_client is None:
                raise HTTPException(status_code=503, detail="Asterisk adapter unavailable")
            return await CallOrchestrator(session, _asterisk_client).create_conference(
                source=request.source,
                targets=request.targets,
                mode=mode,
                conference_id=conference_id,
            )
        return await CallService(session).initiate_conference(
            source=request.source,
            targets=request.targets,
            mode=mode,
            conference_id=conference_id,
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Asterisk conference setup failed: {exc}") from exc


async def _participant_action(call_id: str, extension: str, actor: str, action: str, session: AsyncSession) -> ParticipantStatus:
    call_uuid = _parse_call_id(call_id)
    service = CallService(session)
    method = {"connect": service.connect_participant, "mute": service.mute_participant, "unmute": service.unmute_participant, "remove": service.remove_participant}[action]
    try:
        if action in {"mute", "unmute", "remove"}:
            if _asterisk_client is None:
                raise HTTPException(status_code=503, detail="Asterisk adapter unavailable")

            # Participant control must use the channel identity persisted from ARI
            # events. The adapter's in-memory call/channel map is intentionally not
            # authoritative because Core may restart while an Asterisk call survives.
            result = await session.execute(
                select(CallParticipant).where(
                    CallParticipant.call_id == call_uuid,
                    CallParticipant.extension == extension,
                    CallParticipant.disconnected_at.is_(None),
                )
            )
            participant = result.scalar_one_or_none()
            if participant is None:
                raise HTTPException(status_code=404, detail=f"participant {extension} is not active in call {call_id}")
            channel_id = participant.asterisk_channel_id
            if not channel_id:
                # The participant row is created before its ARI StasisStart event.
                # If Core missed that event, reconcile the participant against the
                # live ARI endpoint before rejecting a control operation.
                recovered_channel = await _asterisk_client.find_active_channel(call_uuid, extension)
                if not recovered_channel:
                    raise HTTPException(status_code=409, detail=f"participant {extension} has no active Asterisk channel")
                await session.rollback()
                async with session.begin():
                    refreshed = await session.get(CallParticipant, participant.id)
                    if refreshed is None or refreshed.disconnected_at is not None:
                        raise HTTPException(status_code=404, detail=f"participant {extension} is no longer active")
                    refreshed.asterisk_channel_id = recovered_channel
                channel_id = recovered_channel

            try:
                if action == "mute":
                    await _asterisk_client.mute_channel(channel_id)
                elif action == "unmute":
                    await _asterisk_client.unmute_channel(channel_id)
                else:
                    await _asterisk_client.remove_channel(channel_id)
            except AsteriskAdapterError as exc:
                # A Core restart or a missed StasisEnd can leave a stale
                # channel ID in the DB. Reconcile it against the live ARI
                # channel list and retry the requested operation once.
                if "HTTP 404" not in str(exc):
                    raise
                recovered_channel = await _asterisk_client.find_active_channel(call_uuid, extension)
                if not recovered_channel:
                    raise
                await session.rollback()
                async with session.begin():
                    refreshed = await session.get(CallParticipant, participant.id)
                    if refreshed is None or refreshed.disconnected_at is not None:
                        raise HTTPException(status_code=404, detail=f"participant {extension} is no longer active")
                    refreshed.asterisk_channel_id = recovered_channel
                channel_id = recovered_channel
                if action == "mute":
                    await _asterisk_client.mute_channel(channel_id)
                elif action == "unmute":
                    await _asterisk_client.unmute_channel(channel_id)
                else:
                    await _asterisk_client.remove_channel(channel_id)

            # Close the read transaction before the service method opens its own.
            await session.rollback()

        await method(call_uuid, extension, actor)
    except HTTPException:
        raise
    except AsteriskAdapterError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        status = 404 if "not in call" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    participant = next((item for item in await service.participants(call_uuid) if item.extension == extension), None)
    if participant is None:
        raise HTTPException(status_code=404, detail=f"participant {extension} not found")
    return _participant_status(call_uuid, participant)


def _participant_status(call_id: UUID, participant) -> ParticipantStatus:
    return ParticipantStatus(call_id=str(call_id), extension=participant.extension, role=participant.role, muted=participant.muted, connected=participant.connected_at is not None and participant.disconnected_at is None)
