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
from .models import Section, Station
from .recording import recording_loop
from .schemas import SectionOut, StationOut

app = FastAPI(title="TCCS Controller API", version="0.5.0")

allowed_origins = ["http://localhost:5173", "http://127.0.0.1:5173", "http://192.168.1.21:5173"]
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

@app.get("/api/v1/stations", response_model=List[StationOut])
async def list_stations(db: AsyncSession = Depends(get_db)) -> List[Station]:
    result = await db.execute(select(Station).where(Station.enabled.is_(True)).order_by(Station.priority, Station.station_number))
    return list(result.scalars().all())

@app.put("/api/v1/stations/order")
async def reorder_stations(payload: dict = Body(...), db: AsyncSession = Depends(get_db)):
    station_ids = payload.get("station_ids")
    if not isinstance(station_ids, list) or not station_ids:
        raise HTTPException(status_code=400, detail="station_ids must be a non-empty list")
    for index, station_id in enumerate(station_ids, start=1):
        station = await db.get(Station, int(station_id))
        if station is not None and station.enabled:
            station.priority = index * 10
    await db.commit()
    return {"status": "OK", "station_ids": station_ids}

@app.get("/api/v1/sections", response_model=List[SectionOut])
async def list_sections(db: AsyncSession = Depends(get_db)) -> List[Section]:
    result = await db.execute(select(Section).order_by(Section.id))
    return list(result.scalars().all())

@app.get("/api/v1/groups")
async def list_groups(db: AsyncSession = Depends(get_db)):
    result = await db.execute(text("""
        SELECT g.id,g.code,g.name,g.section_id,
        COALESCE(json_agg(json_build_object('id',s.id,'station_number',s.station_number,'name',s.name,'sip_extension',s.sip_extension) ORDER BY s.priority,s.station_number) FILTER (WHERE s.id IS NOT NULL),'[]'::json) AS members
        FROM station_groups g
        LEFT JOIN station_group_members gm ON gm.group_id=g.id
        LEFT JOIN stations s ON s.id=gm.station_id AND s.enabled=TRUE
        WHERE g.enabled=TRUE GROUP BY g.id,g.code,g.name,g.section_id ORDER BY g.id
    """))
    return [dict(row._mapping) for row in result]

async def _sync_incoming_controller_calls(db: AsyncSession) -> None:
    channels = await active_channel_details()
    incoming = [c for c in channels if c.get("context")=="tccs-stations" and c.get("dialed_extension")=="9999" and c.get("application")=="CONFBRIDGE" and re.fullmatch(r"10\d{2}",c.get("extension","").strip())]
    active_channels={c["channel"] for c in incoming if c.get("channel")}
    now=datetime.now(timezone.utc)
    result=await db.execute(text("SELECT id,asterisk_channel,originated_at,answered_at FROM call_history WHERE call_type='INCOMING' AND ended_at IS NULL"))
    for row in result:
        if not row.asterisk_channel or row.asterisk_channel not in active_channels:
            await db.execute(text("UPDATE call_history SET status='ENDED',ended_at=:now,duration_seconds=GREATEST(0,EXTRACT(EPOCH FROM (:now-COALESCE(answered_at,originated_at)))::INTEGER) WHERE id=:id"),{"now":now,"id":row.id})
    for channel in incoming:
        source_extension=channel["extension"].strip(); channel_name=channel["channel"].strip()
        station_result=await db.execute(select(Station).where(Station.sip_extension==source_extension,Station.enabled.is_(True)))
        station=station_result.scalar_one_or_none()
        if station is None: continue
        existing=await db.execute(text("SELECT id FROM call_history WHERE call_type='INCOMING' AND asterisk_channel=:asterisk_channel AND ended_at IS NULL LIMIT 1"),{"asterisk_channel":channel_name})
        if existing.first() is not None: continue
        await db.execute(text("""
            INSERT INTO call_history (call_type,source_extension,target_station_id,target_station_number,target_name,status,originated_at,answered_at,asterisk_channel)
            VALUES ('INCOMING',:source_extension,:station_id,:station_number,:target_name,'ANSWERED',:now,:now,:asterisk_channel)
        """),{"source_extension":source_extension,"station_id":station.id,"station_number":station.station_number,"target_name":station.name,"now":now,"asterisk_channel":channel_name})
    await db.commit()

@app.get("/api/v1/asterisk/endpoints")
async def asterisk_endpoints(): return await endpoint_status()

@app.get("/api/v1/call-history")
async def call_history(limit:int=100,db:AsyncSession=Depends(get_db)):
    limit=max(1,min(limit,500)); await _sync_incoming_controller_calls(db)
    result=await db.execute(text("SELECT id,call_type,source_extension,target_station_number,target_name,group_code,status,originated_at,answered_at,ended_at,duration_seconds FROM call_history ORDER BY originated_at DESC LIMIT :limit"),{"limit":limit})
    return [dict(row._mapping) for row in result]

@app.post("/api/v1/calls/stations/{station_id}")
async def call_station_endpoint(station_id:int,payload:Optional[dict]=Body(default=None),db:AsyncSession=Depends(get_db)):
    station=await db.get(Station,station_id)
    if station is None or not station.enabled: raise HTTPException(status_code=404,detail="Station not found")
    payload=payload or {}; call_type=str(payload.get("call_type") or "DIRECT").upper()
    if call_type not in {"DIRECT","GENERAL","SECTION","GROUP"}: raise HTTPException(status_code=400,detail="Invalid call_type")
    group_code=payload.get("group_code"); group_code=str(group_code)[:32] if group_code is not None else None
    try: response=await call_station(station.sip_extension)
    except Exception as exc: raise HTTPException(status_code=409,detail=str(exc))
    history_result=await db.execute(text("INSERT INTO call_history (call_type,source_extension,target_station_id,target_station_number,target_name,group_code,status) VALUES (:call_type,'9999',:station_id,:station_number,:target_name,:group_code,'ORIGINATED') RETURNING id"),{"call_type":call_type,"station_id":station.id,"station_number":station.station_number,"target_name":station.name,"group_code":group_code})
    history=history_result.first(); await db.commit()
    return {"status":"ORIGINATED","station_id":station.id,"sip_extension":station.sip_extension,"history_id":history.id if history else None,"ami_response":response}

@app.post("/api/v1/conference/stations/{station_id}/mute")
async def mute_station(station_id:int,db:AsyncSession=Depends(get_db)):
    station=await db.get(Station,station_id)
    if station is None or not station.enabled: raise HTTPException(status_code=404,detail="Station not found")
    try: response=await mute_conference_channel(station.sip_extension,"SECTION01",True)
    except Exception as exc: raise HTTPException(status_code=409,detail=str(exc))
    return {"status":"MUTED","station_id":station.id,"sip_extension":station.sip_extension,"ami_response":response}

@app.post("/api/v1/conference/stations/{station_id}/unmute")
async def unmute_station(station_id:int,db:AsyncSession=Depends(get_db)):
    station=await db.get(Station,station_id)
    if station is None or not station.enabled: raise HTTPException(status_code=404,detail="Station not found")
    try: response=await mute_conference_channel(station.sip_extension,"SECTION01",False)
    except Exception as exc: raise HTTPException(status_code=409,detail=str(exc))
    return {"status":"UNMUTED","station_id":station.id,"sip_extension":station.sip_extension,"ami_response":response}

@app.post("/api/v1/conference/stations/{station_id}/hangup")
async def hangup_station(station_id:int,db:AsyncSession=Depends(get_db)):
    station=await db.get(Station,station_id)
    if station is None or not station.enabled: raise HTTPException(status_code=404,detail="Station not found")
    try: response=await hangup_station_channel(station.sip_extension)
    except Exception as exc: raise HTTPException(status_code=409,detail=str(exc))
    ended_at=datetime.now(timezone.utc)
    await db.execute(text("UPDATE call_history SET status='ENDED',ended_at=:ended_at,duration_seconds=GREATEST(0,EXTRACT(EPOCH FROM (:ended_at-COALESCE(answered_at,originated_at)))::INTEGER) WHERE id=(SELECT id FROM call_history WHERE target_station_id=:station_id AND ended_at IS NULL ORDER BY originated_at DESC LIMIT 1)"),{"station_id":station.id,"ended_at":ended_at})
    await db.commit()
    return {"status":"HUNG_UP","station_id":station.id,"sip_extension":station.sip_extension,"ami_response":response}

@app.get("/api/v1/stations/{station_id}",response_model=StationOut)
async def get_station(station_id:int,db:AsyncSession=Depends(get_db))->Station:
    station=await db.get(Station,station_id)
    if station is None or not station.enabled: raise HTTPException(status_code=404,detail="Station not found")
    return station

# Subscriber / station management. This API deliberately uses a separate prefix
# so it cannot conflict with the existing /stations/{station_id} route.
def _validate_station_payload(payload:dict,existing_id:Optional[int]=None)->dict:
    station_number=str(payload.get("station_number") or "").strip()
    name=str(payload.get("name") or "").strip()
    sip=str(payload.get("sip_extension") or "").strip()
    if not station_number: raise HTTPException(status_code=400,detail="Station number is required")
    if not name: raise HTTPException(status_code=400,detail="Station name is required")
    if not re.fullmatch(r"10\d{2}",sip): raise HTTPException(status_code=400,detail="SIP extension must be a 4-digit 10xx extension")
    try: section_id=int(payload.get("section_id"))
    except (TypeError,ValueError): raise HTTPException(status_code=400,detail="Valid section_id is required")
    return {"station_number":station_number[:32],"name":name[:128],"location":(str(payload.get("location") or "").strip() or None)[:256],"section_id":section_id,"sip_extension":sip[:64],"station_type":str(payload.get("station_type") or "WAY_STATION")[:32],"enabled":bool(payload.get("enabled",True)),"existing_id":existing_id}

@app.get("/api/v1/station-management")
async def manage_list_stations(db:AsyncSession=Depends(get_db)):
    result=await db.execute(select(Station).order_by(Station.priority,Station.station_number))
    return [dict(s.__dict__) | {"_sa_instance_state":None} for s in result.scalars().all()]

@app.post("/api/v1/station-management")
async def manage_create_station(payload:dict=Body(...),db:AsyncSession=Depends(get_db)):
    data=_validate_station_payload(payload)
    section=await db.get(Section,data["section_id"])
    if section is None: raise HTTPException(status_code=400,detail="Section not found")
    duplicate=await db.execute(select(Station).where((Station.station_number==data["station_number"]) | (Station.sip_extension==data["sip_extension"])))
    existing=duplicate.scalars().first()
    if existing: raise HTTPException(status_code=409,detail="Station number or SIP extension already exists")
    station=Station(station_number=data["station_number"],name=data["name"],location=data["location"],section_id=data["section_id"],sip_extension=data["sip_extension"],station_type=data["station_type"],enabled=data["enabled"],priority=100)
    db.add(station); await db.commit(); await db.refresh(station)
    return station

@app.put("/api/v1/station-management/{station_id}")
async def manage_update_station(station_id:int,payload:dict=Body(...),db:AsyncSession=Depends(get_db)):
    station=await db.get(Station,station_id)
    if station is None: raise HTTPException(status_code=404,detail="Station not found")
    merged={"station_number":payload.get("station_number",station.station_number),"name":payload.get("name",station.name),"location":payload.get("location",station.location),"section_id":payload.get("section_id",station.section_id),"sip_extension":payload.get("sip_extension",station.sip_extension),"station_type":payload.get("station_type",station.station_type),"enabled":payload.get("enabled",station.enabled)}
    data=_validate_station_payload(merged,station_id)
    section=await db.get(Section,data["section_id"])
    if section is None: raise HTTPException(status_code=400,detail="Section not found")
    duplicate=await db.execute(select(Station).where(((Station.station_number==data["station_number"]) | (Station.sip_extension==data["sip_extension"])) & (Station.id!=station_id)))
    if duplicate.scalars().first(): raise HTTPException(status_code=409,detail="Station number or SIP extension already exists")
    for key in ("station_number","name","location","section_id","sip_extension","station_type","enabled"): setattr(station,key,data[key])
    await db.commit(); await db.refresh(station)
    return station

@app.delete("/api/v1/station-management/{station_id}")
async def manage_delete_station(station_id:int,db:AsyncSession=Depends(get_db)):
    station=await db.get(Station,station_id)
    if station is None: raise HTTPException(status_code=404,detail="Station not found")
    channels=await active_channel_details()
    active=any(c.get("extension")==station.sip_extension and c.get("channel") for c in channels)
    if active: raise HTTPException(status_code=409,detail="Station has an active call; disconnect it before removing the subscriber")
    station.enabled=False
    await db.commit()
    return {"status":"REMOVED","station_id":station.id,"station_number":station.station_number}
