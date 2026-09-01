from __future__ import annotations

import os
from typing import List, Optional

from fastapi import Body, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .asterisk import endpoint_status
from .ami import hangup_conference_channel, mute_conference_channel
from .calls import call_station
from .db import get_db
from .models import Section, Station
from .schemas import SectionOut, StationOut

app = FastAPI(title="TCCS Controller API", version="0.3.7")

allowed_origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://192.168.1.21:5173",
]
frontend_origin = os.getenv("TCCS_FRONTEND_ORIGIN")
if frontend_origin:
    allowed_origins.append(frontend_origin.rstrip("/"))

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "OPTIONS"],
    allow_headers=["*"],
)

@app.get("/health")
def health():
    return {"status": "ok", "service": "tccs-controller-api"}

@app.get("/api/v1/sections", response_model=List[SectionOut])
async def list_sections(db: AsyncSession = Depends(get_db)) -> List[Section]:
    result = await db.scalars(select(Section).where(Section.enabled.is_(True)).order_by(Section.code))
    return list(result.all())

@app.get("/api/v1/stations", response_model=List[StationOut])
async def list_stations(section_id: Optional[int] = None, db: AsyncSession = Depends(get_db)) -> List[Station]:
    query = select(Station).where(Station.enabled.is_(True))
    if section_id is not None:
        query = query.where(Station.section_id == section_id)
    result = await db.scalars(query.order_by(Station.priority, Station.station_number))
    return list(result.all())

@app.put("/api/v1/stations/order")
async def save_station_order(station_ids: List[int] = Body(..., embed=True), db: AsyncSession = Depends(get_db)):
    if not station_ids or len(station_ids) != len(set(station_ids)):
        raise HTTPException(status_code=400, detail="station_ids must be non-empty and unique")
    stations = list((await db.scalars(select(Station).where(Station.id.in_(station_ids), Station.enabled.is_(True)))).all())
    if {station.id for station in stations} != set(station_ids):
        raise HTTPException(status_code=400, detail="One or more stations are invalid or disabled")
    station_map = {station.id: station for station in stations}
    for index, station_id in enumerate(station_ids, start=1):
        station_map[station_id].priority = index * 10
    await db.commit()
    return {"status": "ok", "station_ids": station_ids}

@app.get("/api/v1/asterisk/endpoints")
async def asterisk_endpoints():
    return await endpoint_status()

@app.post("/api/v1/calls/stations/{station_id}")
async def originate_station_call(station_id: int, db: AsyncSession = Depends(get_db)):
    station = await db.get(Station, station_id)
    if station is None or not station.enabled:
        raise HTTPException(status_code=404, detail="Station not found")
    statuses = await endpoint_status()
    status_by_ext = {item["sip_extension"]: item["status"] for item in statuses}
    if status_by_ext.get(station.sip_extension) != "REGISTERED":
        raise HTTPException(status_code=409, detail="Station SIP endpoint is not registered")
    try:
        response = await call_station(station.sip_extension)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Asterisk originate failed: {exc}")
    return {"status": "ORIGINATED", "station_id": station.id, "station_number": station.station_number, "sip_extension": station.sip_extension, "ami_response": response}

@app.post("/api/v1/conference/stations/{station_id}/mute")
async def mute_station(station_id: int, db: AsyncSession = Depends(get_db)):
    station = await db.get(Station, station_id)
    if station is None or not station.enabled:
        raise HTTPException(status_code=404, detail="Station not found")
    try:
        response = await mute_conference_channel(station.sip_extension, "SECTION01", True)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"status": "MUTED", "station_id": station.id, "sip_extension": station.sip_extension, "ami_response": response}

@app.post("/api/v1/conference/stations/{station_id}/unmute")
async def unmute_station(station_id: int, db: AsyncSession = Depends(get_db)):
    station = await db.get(Station, station_id)
    if station is None or not station.enabled:
        raise HTTPException(status_code=404, detail="Station not found")
    try:
        response = await mute_conference_channel(station.sip_extension, "SECTION01", False)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"status": "UNMUTED", "station_id": station.id, "sip_extension": station.sip_extension, "ami_response": response}

@app.post("/api/v1/conference/stations/{station_id}/hangup")
async def hangup_station(station_id: int, db: AsyncSession = Depends(get_db)):
    station = await db.get(Station, station_id)
    if station is None or not station.enabled:
        raise HTTPException(status_code=404, detail="Station not found")
    try:
        response = await hangup_conference_channel(station.sip_extension, "SECTION01")
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"status": "HUNG_UP", "station_id": station.id, "sip_extension": station.sip_extension, "ami_response": response}

@app.get("/api/v1/stations/{station_id}", response_model=StationOut)
async def get_station(station_id: int, db: AsyncSession = Depends(get_db)) -> Station:
    station = await db.get(Station, station_id)
    if station is None or not station.enabled:
        raise HTTPException(status_code=404, detail="Station not found")
    return station
