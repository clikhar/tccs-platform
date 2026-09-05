from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import Body, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .asterisk import active_channel_details, endpoint_status
from .ami import hangup_station_channel, mute_conference_channel
from .calls import call_station
from .db import SessionLocal, get_db
from .master import ensure_master_tables, require_admin, router as master_router
from .models import Section, Station
from .recording import recording_loop
from .schemas import SectionOut, StationOut
from .emergency import ensure_emergency_tables, router as emergency_router
from .server_management import router as server_management_router
from .user_management import require_user

app = FastAPI(title="TCCS Controller API", version="0.5.0")
app.include_router(master_router)
app.include_router(server_management_router)
app.include_router(emergency_router)

allowed_origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://192.168.1.21:5173",
    "http://100.93.101.10:5173",
]
frontend_origin = os.getenv("TCCS_FRONTEND_ORIGIN")
if frontend_origin:
    allowed_origins.append(frontend_origin.rstrip("/"))
app.add_middleware(CORSMiddleware, allow_origins=allowed_origins, allow_credentials=False, allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"], allow_headers=["*"])

@app.on_event("startup")
async def ensure_call_history_table() -> None:
    async with SessionLocal() as db:
        await db.execute(text("""
            CREATE TABLE IF NOT EXISTS call_history (
                id BIGSERIAL PRIMARY KEY,
                call_type VARCHAR(32) NOT NULL,
                source_extension VARCHAR(64) NOT NULL DEFAULT '9999',
                target_station_id BIGINT REFERENCES stations(id) ON DELETE SET NULL,
                target_station_number VARCHAR(32),
                target_name VARCHAR(128),
                group_code VARCHAR(32),
                status VARCHAR(32) NOT NULL DEFAULT 'ORIGINATED',
                originated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                answered_at TIMESTAMPTZ,
                ended_at TIMESTAMPTZ,
                duration_seconds INTEGER,
                asterisk_channel VARCHAR(128)
            )
        """))
        await db.execute(text("ALTER TABLE call_history ADD COLUMN IF NOT EXISTS asterisk_channel VARCHAR(128)"))
        await db.execute(text("CREATE INDEX IF NOT EXISTS idx_call_history_originated_at ON call_history(originated_at DESC)"))
        await db.execute(text("CREATE INDEX IF NOT EXISTS idx_call_history_target_station ON call_history(target_station_id)"))
        await db.execute(text("CREATE INDEX IF NOT EXISTS idx_call_history_status ON call_history(status)"))
        await db.commit()

@app.on_event("startup")
async def ensure_master_schema() -> None:
    async with SessionLocal() as db:
        await ensure_master_tables(db)

@app.on_event("startup")
async def ensure_emergency_schema() -> None:
    async with SessionLocal() as db:
        await ensure_emergency_tables(db)

@app.on_event("startup")
async def start_recording_worker() -> None:
    app.state.recording_task = asyncio.create_task(recording_loop())

@app.on_event("shutdown")
async def stop_recording_worker() -> None:
    task = getattr(app.state, "recording_task", None)
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

@app.get("/api/v1/health")
async def health() -> dict:
    return {"status": "ok"}

@app.get("/api/v1/sections", response_model=List[SectionOut])
async def sections(db: AsyncSession = Depends(get_db)) -> List[Section]:
    result = await db.execute(select(Section).where(Section.enabled.is_(True)).order_by(Section.id))
    return list(result.scalars().all())

@app.get("/api/v1/stations", response_model=List[StationOut])
async def stations(db: AsyncSession = Depends(get_db)) -> List[Station]:
    result = await db.execute(select(Station).where(Station.enabled.is_(True)).order_by(Station.priority, Station.station_number))
    return list(result.scalars().all())

@app.get("/api/v1/asterisk/endpoints")
async def asterisk_endpoints() -> List[dict]:
    return await endpoint_status()

@app.get("/api/v1/asterisk/channels")
async def asterisk_channels() -> dict:
    channels = await active_channel_details()
    return {"channels": channels}

@app.post("/api/v1/calls/direct")
async def direct_call(payload: dict = Body(...), db: AsyncSession = Depends(get_db), admin: dict = Depends(require_admin)) -> dict:
    station_id = payload.get("station_id")
    try:
        station_id = int(station_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Valid station_id is required")
    station = await db.get(Station, station_id)
    if station is None or not station.enabled:
        raise HTTPException(status_code=404, detail="Station not found")
    try:
        return await call_station(station.sip_extension)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

async def _authorize_controller_station(station: Station, user: dict, db: AsyncSession) -> None:
    if user.get("role") != "CONTROLLER":
        return
    controller_id = user.get("controller_id")
    if not controller_id:
        raise HTTPException(status_code=403, detail="Controller is not assigned")
    row = (await db.execute(text("SELECT section_id FROM controllers WHERE id=:id AND enabled=TRUE"), {"id": controller_id})).first()
    if row is None or row.section_id != station.section_id:
        raise HTTPException(status_code=403, detail="Station is outside the controller's assigned section")

@app.post("/api/v1/calls/stations/{station_id}")
async def controller_station_call(station_id: int, payload: dict = Body(default={}), db: AsyncSession = Depends(get_db), user: dict = Depends(require_user)) -> dict:
    station = await db.get(Station, station_id)
    if station is None or not station.enabled:
        raise HTTPException(status_code=404, detail="Station not found")
    await _authorize_controller_station(station, user, db)
    call_type = str(payload.get("call_type") or "DIRECT").strip().upper()
    if call_type not in {"DIRECT", "GENERAL", "SECTION", "GROUP"}:
        raise HTTPException(status_code=400, detail="Invalid call_type")
    group_code = payload.get("group_code")
    if group_code is not None:
        group_code = str(group_code).strip() or None
    try:
        result = await call_station(station.sip_extension)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    source_extension = str(payload.get("source_extension") or "").strip()
    if not source_extension and user.get("controller_id"):
        source = (await db.execute(text("SELECT sa.extension FROM controllers c JOIN sip_accounts sa ON sa.id=c.sip_account_id WHERE c.id=:id LIMIT 1"), {"id": user["controller_id"]})).first()
        source_extension = source.extension if source else ""
    if not source_extension:
        source_extension = "9999"
    await db.execute(text("""
        INSERT INTO call_history (call_type, source_extension, target_station_id, target_station_number, target_name, group_code, status, originated_at)
        VALUES (:call_type, :source_extension, :target_station_id, :target_station_number, :target_name, :group_code, 'ORIGINATED', :originated_at)
    """), {"call_type": call_type, "source_extension": source_extension, "target_station_id": station.id, "target_station_number": station.station_number, "target_name": station.name, "group_code": group_code, "originated_at": datetime.now(timezone.utc)})
    await db.commit()
    return result

@app.post("/api/v1/conference/stations/{station_id}/hangup")
async def conference_station_hangup(station_id: int, db: AsyncSession = Depends(get_db), user: dict = Depends(require_user)) -> dict:
    station = await db.get(Station, station_id)
    if station is None or not station.enabled:
        raise HTTPException(status_code=404, detail="Station not found")
    await _authorize_controller_station(station, user, db)
    try:
        result = await hangup_station_channel(station.sip_extension)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return result

@app.post("/api/v1/conference/stations/{station_id}/mute")
async def conference_station_mute(station_id: int, db: AsyncSession = Depends(get_db), user: dict = Depends(require_user)) -> dict:
    station = await db.get(Station, station_id)
    if station is None or not station.enabled:
        raise HTTPException(status_code=404, detail="Station not found")
    await _authorize_controller_station(station, user, db)
    try:
        result = await mute_conference_channel(station.sip_extension, mute=True)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return result

@app.post("/api/v1/conference/stations/{station_id}/unmute")
async def conference_station_unmute(station_id: int, db: AsyncSession = Depends(get_db), user: dict = Depends(require_user)) -> dict:
    station = await db.get(Station, station_id)
    if station is None or not station.enabled:
        raise HTTPException(status_code=404, detail="Station not found")
    await _authorize_controller_station(station, user, db)
    try:
        result = await mute_conference_channel(station.sip_extension, mute=False)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return result

@app.get("/api/v1/station-management")
async def station_management_list(db: AsyncSession = Depends(get_db), admin: dict = Depends(require_admin)):
    result = await db.execute(text("SELECT s.id,s.station_number,s.name,s.location,s.section_id,s.sip_extension,s.station_type,s.enabled,s.priority,sec.code AS section_code,sec.name AS section_name FROM stations s LEFT JOIN sections sec ON sec.id=s.section_id ORDER BY s.priority,s.station_number"))
    registered = {c.get("extension") for c in await active_channel_details()}
    return [{**dict(row._mapping), "registered": row.sip_extension in registered} for row in result]

def _validate_station_payload(payload: dict, existing_id: Optional[int] = None) -> dict:
    station_number = str(payload.get("station_number") or "").strip()
    name = str(payload.get("name") or "").strip()
    sip = str(payload.get("sip_extension") or "").strip()
    if not station_number:
        raise HTTPException(status_code=400, detail="Station number is required")
    if not name:
        raise HTTPException(status_code=400, detail="Station name is required")
    if not re.fullmatch(r"10\d{2}", sip):
        raise HTTPException(status_code=400, detail="SIP extension must be a 4-digit 10xx extension")
    try:
        section_id = int(payload.get("section_id"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Valid section_id is required")
    return {"station_number": station_number, "name": name, "location": str(payload.get("location") or "").strip(), "section_id": section_id, "sip_extension": sip, "station_type": str(payload.get("station_type") or "OTHER").strip().upper(), "enabled": bool(payload.get("enabled", True)), "priority": int(payload.get("priority") or 100)}

@app.post("/api/v1/station-management")
async def manage_create_station(payload: dict = Body(...), db: AsyncSession = Depends(get_db), admin: dict = Depends(require_admin)):
    values = _validate_station_payload(payload)
    if (await db.execute(text("SELECT id FROM sections WHERE id=:id"), {"id": values["section_id"]})).first() is None:
        raise HTTPException(status_code=400, detail="Section not found")
    if (await db.execute(text("SELECT id FROM stations WHERE station_number=:number"), {"number": values["station_number"]})).first() is not None:
        raise HTTPException(status_code=409, detail="Station number already exists")
    if (await db.execute(text("SELECT id FROM stations WHERE sip_extension=:sip"), {"sip": values["sip_extension"]})).first() is not None:
        raise HTTPException(status_code=409, detail="SIP extension already exists")
    result = await db.execute(text("INSERT INTO stations(station_number,name,location,section_id,sip_extension,station_type,enabled,priority) VALUES(:station_number,:name,:location,:section_id,:sip_extension,:station_type,:enabled,:priority) RETURNING id,station_number,name,location,section_id,sip_extension,station_type,enabled,priority"), values)
    await db.commit()
    return dict(result.first()._mapping)

@app.put("/api/v1/station-management/{station_id}")
async def manage_update_station(station_id: int, payload: dict = Body(...), db: AsyncSession = Depends(get_db), admin: dict = Depends(require_admin)):
    current = (await db.execute(text("SELECT * FROM stations WHERE id=:id"), {"id": station_id})).first()
    if current is None:
        raise HTTPException(status_code=404, detail="Station not found")
    values = _validate_station_payload(payload, station_id)
    if (await db.execute(text("SELECT id FROM sections WHERE id=:id"), {"id": values["section_id"]})).first() is None:
        raise HTTPException(status_code=400, detail="Section not found")
    duplicate = (await db.execute(text("SELECT id FROM stations WHERE (station_number=:number OR sip_extension=:sip) AND id<>:id"), {"number": values["station_number"], "sip": values["sip_extension"], "id": station_id})).first()
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="Station number or SIP extension already exists")
    active = await active_channel_details()
    if values["sip_extension"] != current.sip_extension and any(c.get("extension") == current.sip_extension for c in active):
        raise HTTPException(status_code=409, detail="Cannot change SIP extension while the subscriber has an active call")
    result = await db.execute(text("UPDATE stations SET station_number=:station_number,name=:name,location=:location,section_id=:section_id,sip_extension=:sip_extension,station_type=:station_type,enabled=:enabled,priority=:priority WHERE id=:id RETURNING id,station_number,name,location,section_id,sip_extension,station_type,enabled,priority"), {**values, "id": station_id})
    await db.commit()
    return dict(result.first()._mapping)

@app.delete("/api/v1/station-management/{station_id}")
async def manage_delete_station(station_id: int, db: AsyncSession = Depends(get_db), admin: dict = Depends(require_admin)):
    station = await db.get(Station, station_id)
    if station is None:
        raise HTTPException(status_code=404, detail="Station not found")
    active = await active_channel_details()
    if any(c.get("extension") == station.sip_extension for c in active):
        raise HTTPException(status_code=409, detail="Cannot remove subscriber while it has an active call")
    station.enabled = False
    await db.commit()
    return {"status": "REMOVED", "station_id": station.id}

@app.get("/api/v1/asterisk/active-calls")
async def active_calls() -> dict:
    return {"channels": await active_channel_details()}
