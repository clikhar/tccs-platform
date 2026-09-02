from __future__ import annotations

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
from .schemas import SectionOut, StationOut

app = FastAPI(title="TCCS Controller API", version="0.4.2")

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
                duration_seconds INTEGER
            )
        """))
        await db.execute(text("CREATE INDEX IF NOT EXISTS idx_call_history_originated_at ON call_history(originated_at DESC)"))
        await db.execute(text("CREATE INDEX IF NOT EXISTS idx_call_history_target_station ON call_history(target_station_id)"))
        await db.execute(text("CREATE INDEX IF NOT EXISTS idx_call_history_status ON call_history(status)"))
        await db.commit()


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
    """))
    return [dict(row._mapping) for row in result]


async def _sync_incoming_controller_calls(db: AsyncSession) -> None:
    """Create history records for live station calls entering controller 9999.

    The live channel is the station's PJSIP channel (for example PJSIP/1001),
    not a PJSIP/9999 channel.  The station channel runs in tccs-stations and
    enters SECTION01 through ConfBridge, so the channel extension itself is
    the reliable source of the originating station extension.
    """
    channels = await active_channel_details()
    incoming = [
        channel for channel in channels
        if channel.get("context") == "tccs-stations"
        and channel.get("application") == "CONFBRIDGE"
    ]

    for channel in incoming:
        source_extension = channel.get("extension", "").strip()
        if not re.fullmatch(r"10\d{2}", source_extension):
            continue

        station_result = await db.execute(
            select(Station).where(
                Station.sip_extension == source_extension,
                Station.enabled.is_(True),
            )
        )
        station = station_result.scalar_one_or_none()
        if station is None:
            continue

        existing = await db.execute(text("""
            SELECT id
            FROM call_history
            WHERE call_type = 'INCOMING'
              AND source_extension = :source_extension
              AND target_station_id = :station_id
              AND ended_at IS NULL
            ORDER BY originated_at DESC
            LIMIT 1
        """), {
            "source_extension": source_extension,
            "station_id": station.id,
        })
        if existing.first() is not None:
            continue

        now = datetime.now(timezone.utc)
        await db.execute(text("""
            INSERT INTO call_history (
                call_type,
                source_extension,
                target_station_id,
                target_station_number,
                target_name,
                status,
                originated_at,
                answered_at
            ) VALUES (
                'INCOMING',
                :source_extension,
                :station_id,
                :station_number,
                :target_name,
                'ANSWERED',
                :now,
                :now
            )
        """), {
            "source_extension": source_extension,
            "station_id": station.id,
            "station_number": station.station_number,
            "target_name": station.name,
            "now": now,
        })
    await db.commit()


@app.get("/api/v1/asterisk/endpoints")
async def asterisk_endpoints():
    return await endpoint_status()


@app.get("/api/v1/call-history")
async def call_history(limit: int = 100, db: AsyncSession = Depends(get_db)):
    limit = max(1, min(limit, 500))
    await _sync_incoming_controller_calls(db)
    result = await db.execute(text("""
        SELECT
            id,
            call_type,
            source_extension,
            target_station_number,
            target_name,
            group_code,
            status,
            originated_at,
            answered_at,
            ended_at,
            duration_seconds
        FROM call_history
        ORDER BY originated_at DESC
        LIMIT :limit
    """), {"limit": limit})
    return [dict(row._mapping) for row in result]


@app.post("/api/v1/calls/stations/{station_id}")
async def call_station_endpoint(station_id: int, payload: Optional[dict] = Body(default=None), db: AsyncSession = Depends(get_db)):
    station = await db.get(Station, station_id)
    if station is None or not station.enabled:
        raise HTTPException(status_code=404, detail="Station not found")

    payload = payload or {}
    call_type = str(payload.get("call_type") or "DIRECT").upper()
    if call_type not in {"DIRECT", "GENERAL", "SECTION", "GROUP"}:
        raise HTTPException(status_code=400, detail="Invalid call_type")
    group_code = payload.get("group_code")
    if group_code is not None:
        group_code = str(group_code)[:32]

    try:
        response = await call_station(station.sip_extension)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    history_result = await db.execute(text("""
        INSERT INTO call_history (
            call_type, source_extension, target_station_id,
            target_station_number, target_name, group_code, status
        ) VALUES (
            :call_type, '9999', :station_id,
            :station_number, :target_name, :group_code, 'ORIGINATED'
        )
        RETURNING id
    """), {
        "call_type": call_type,
        "station_id": station.id,
        "station_number": station.station_number,
        "target_name": station.name,
        "group_code": group_code,
    })
    history = history_result.first()
    await db.commit()

    return {
        "status": "ORIGINATED",
        "station_id": station.id,
        "sip_extension": station.sip_extension,
        "history_id": history.id if history else None,
        "ami_response": response,
    }


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
        response = await hangup_station_channel(station.sip_extension)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    ended_at = datetime.now(timezone.utc)
    await db.execute(text("""
        UPDATE call_history
        SET status = 'ENDED',
            ended_at = :ended_at,
            duration_seconds = GREATEST(0, EXTRACT(EPOCH FROM (:ended_at - COALESCE(answered_at, originated_at)))::INTEGER)
        WHERE id = (
            SELECT id FROM call_history
            WHERE target_station_id = :station_id AND ended_at IS NULL
            ORDER BY originated_at DESC
            LIMIT 1
        )
    """), {"station_id": station.id, "ended_at": ended_at})
    await db.commit()
    return {"status": "HUNG_UP", "station_id": station.id, "sip_extension": station.sip_extension, "ami_response": response}


@app.get("/api/v1/stations/{station_id}", response_model=StationOut)
async def get_station(station_id: int, db: AsyncSession = Depends(get_db)) -> Station:
    station = await db.get(Station, station_id)
    if station is None or not station.enabled:
        raise HTTPException(status_code=404, detail="Station not found")
    return station
