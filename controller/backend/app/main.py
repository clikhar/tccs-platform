from __future__ import annotations

import os
from typing import List, Optional

from fastapi import Body, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .asterisk import endpoint_status
from .ami import hangup_conference_channel, hangup_station_channel, mute_conference_channel
from .calls import call_station
from .db import get_db
from .models import Section, Station
from .schemas import SectionOut, StationOut

app = FastAPI(title="TCCS Controller API", version="0.3.9")

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
    """Return enabled station groups and their enabled station members.

    The membership table already exists in the application schema, but it is
    intentionally kept as a lightweight association table rather than an ORM
    entity. A parameterized SQL query keeps this API small and avoids changing
    the existing station/group model contracts.
    """
    result = await db.execute(
        text(
            """
            SELECT
                g.id,
                g.code,
                g.name,
                g.section_id,
                COALESCE(
                    json_agg(
                        json_build_object(
                            'id', s.id,
                            'station_number', s.station_number,
                            'name', s.name,
                            'sip_extension', s.sip_extension
                        )
                        ORDER BY s.priority, s.station_number
                    ) FILTER (WHERE s.id IS NOT NULL),
                    '[]'::json
                ) AS members
            FROM station_groups g
            LEFT JOIN station_group_members gm ON gm.group_id = g.id
            LEFT JOIN stations s ON s.id = gm.station_id AND s.enabled = TRUE
            WHERE g.enabled = TRUE
            GROUP BY g.id, g.code, g.name, g.section_id
            ORDER BY g.id
            """
        )
    )
    return [dict(row._mapping) for row in result]


@app.get("/api/v1/asterisk/endpoints")
async def asterisk_endpoints():
    return await endpoint_status()


@app.post("/api/v1/calls/stations/{station_id}")
async def call_station_endpoint(station_id: int, db: AsyncSession = Depends(get_db)):
    station = await db.get(Station, station_id)
    if station is None or not station.enabled:
        raise HTTPException(status_code=404, detail="Station not found")
    try:
        response = await call_station(station.sip_extension)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"status": "ORIGINATED", "station_id": station.id, "sip_extension": station.sip_extension, "ami_response": response}


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
        # END must work both before answer (RINGING) and after the
        # station has entered ConfBridge.
        response = await hangup_station_channel(station.sip_extension)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"status": "HUNG_UP", "station_id": station.id, "sip_extension": station.sip_extension, "ami_response": response}


@app.get("/api/v1/stations/{station_id}", response_model=StationOut)
async def get_station(station_id: int, db: AsyncSession = Depends(get_db)) -> Station:
    station = await db.get(Station, station_id)
    if station is None or not station.enabled:
        raise HTTPException(status_code=404, detail="Station not found")
    return station
