from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .calls import call_station
from .db import get_db
from .models import Station
from .master import router as master_router
from .user_management import router as user_router

router = APIRouter(prefix="/api/v1/emergency", tags=["emergency"])

# user_management contains the shared authentication, role and controller SIP
# management endpoints. Include them in the already-mounted master router so
# the existing /api/v1/master/* API remains backward compatible.
master_router.include_router(user_router)

EMERGENCY_PRIORITY = 1000

async def ensure_emergency_tables(db: AsyncSession) -> None:
    await db.execute(text("""
        CREATE TABLE IF NOT EXISTS emergency_groups (
            id BIGSERIAL PRIMARY KEY,
            code VARCHAR(32) NOT NULL UNIQUE,
            name VARCHAR(128) NOT NULL,
            section_id BIGINT REFERENCES sections(id),
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            priority INTEGER NOT NULL DEFAULT 1000
        )
    """))
    await db.execute(text("""
        CREATE TABLE IF NOT EXISTS emergency_group_members (
            group_id BIGINT NOT NULL REFERENCES emergency_groups(id) ON DELETE CASCADE,
            station_id BIGINT NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
            PRIMARY KEY (group_id, station_id)
        )
    """))
    await db.execute(text("""
        CREATE TABLE IF NOT EXISTS emergency_events (
            id BIGSERIAL PRIMARY KEY,
            group_id BIGINT REFERENCES emergency_groups(id) ON DELETE SET NULL,
            group_code VARCHAR(32),
            source_extension VARCHAR(64) NOT NULL DEFAULT '9999',
            priority INTEGER NOT NULL DEFAULT 1000,
            status VARCHAR(32) NOT NULL DEFAULT 'ORIGINATED',
            originated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            acknowledged_at TIMESTAMPTZ,
            ended_at TIMESTAMPTZ,
            target_count INTEGER NOT NULL DEFAULT 0,
            answered_count INTEGER NOT NULL DEFAULT 0
        )
    """))
    await db.execute(text("CREATE INDEX IF NOT EXISTS idx_emergency_events_originated_at ON emergency_events(originated_at DESC)"))
    await db.commit()

@router.get("/groups")
async def list_emergency_groups(db: AsyncSession = Depends(get_db)):
    result = await db.execute(text("""
        SELECT g.id,g.code,g.name,g.section_id,g.enabled,g.priority,
               COALESCE(json_agg(json_build_object(
                   'id',s.id,'station_number',s.station_number,
                   'name',s.name,'sip_extension',s.sip_extension
               ) ORDER BY s.priority,s.station_number)
               FILTER (WHERE s.id IS NOT NULL),'[]'::json) AS members
        FROM emergency_groups g
        LEFT JOIN emergency_group_members gm ON gm.group_id=g.id
        LEFT JOIN stations s ON s.id=gm.station_id AND s.enabled=TRUE
        GROUP BY g.id,g.code,g.name,g.section_id,g.enabled,g.priority
        ORDER BY g.priority DESC,g.id
    """))
    return [dict(row._mapping) for row in result]

@router.post("/groups")
async def create_emergency_group(payload: dict = Body(...), db: AsyncSession = Depends(get_db)):
    code = str(payload.get("code") or "").strip().upper()
    name = str(payload.get("name") or "").strip()
    if not code or not name:
        raise HTTPException(status_code=400, detail="Emergency group code and name are required")
    try:
        section_id = int(payload["section_id"]) if payload.get("section_id") is not None else None
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid section_id")
    priority = int(payload.get("priority", EMERGENCY_PRIORITY))
    try:
        result = await db.execute(text("""
            INSERT INTO emergency_groups(code,name,section_id,enabled,priority)
            VALUES (:code,:name,:section_id,TRUE,:priority)
            RETURNING id
        """), {"code": code[:32], "name": name[:128], "section_id": section_id, "priority": priority})
        row = result.first()
        await db.commit()
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=f"Unable to create emergency group: {exc}")
    return {"id": row.id, "code": code, "name": name, "section_id": section_id, "enabled": True, "priority": priority}

@router.put("/groups/{group_id}/members")
async def set_emergency_group_members(group_id: int, payload: dict, db: AsyncSession = Depends(get_db)):
    station_ids = payload.get("station_ids")
    if not isinstance(station_ids, list):
        raise HTTPException(status_code=400, detail="station_ids must be a list")
    exists = await db.execute(text("SELECT id FROM emergency_groups WHERE id=:id"), {"id": group_id})
    if exists.first() is None:
        raise HTTPException(status_code=404, detail="Emergency group not found")
    await db.execute(text("DELETE FROM emergency_group_members WHERE group_id=:id"), {"id": group_id})
    for station_id in sorted(set(int(x) for x in station_ids)):
        station = await db.get(Station, station_id)
        if station is None or not station.enabled:
            continue
        await db.execute(text("INSERT INTO emergency_group_members(group_id,station_id) VALUES (:gid,:sid) ON CONFLICT DO NOTHING"), {"gid": group_id, "sid": station_id})
    await db.commit()
    return {"status": "OK", "group_id": group_id, "station_ids": station_ids}

@router.post("/calls/groups/{group_id}")
async def emergency_group_call(group_id: int, db: AsyncSession = Depends(get_db)):
    group_result = await db.execute(text("""
        SELECT g.id,g.code,g.name
        FROM emergency_groups g
        WHERE g.id=:id AND g.enabled=TRUE
    """), {"id": group_id})
    group = group_result.first()
    if group is None:
        raise HTTPException(status_code=404, detail="Emergency group not found or disabled")

    members_result = await db.execute(text("""
        SELECT s.id,s.station_number,s.name,s.sip_extension
        FROM emergency_group_members gm
        JOIN stations s ON s.id=gm.station_id
        WHERE gm.group_id=:id AND s.enabled=TRUE
        ORDER BY s.priority,s.station_number
    """), {"id": group_id})
    members = list(members_result)
    if not members:
        raise HTTPException(status_code=409, detail="Emergency group has no enabled members")

    active_result = await db.execute(text("SELECT extension FROM (SELECT split_part(channel,'-',1) AS extension FROM (SELECT '' AS channel) x) y WHERE FALSE"))
    await active_result.close()

    event_result = await db.execute(text("""
        INSERT INTO emergency_events(group_id,group_code,source_extension,priority,status,target_count)
        VALUES (:group_id,:group_code,'9999',:priority,'ORIGINATED',:target_count)
        RETURNING id
    """), {"group_id": group.id, "group_code": group.code, "priority": EMERGENCY_PRIORITY, "target_count": len(members)})
    event = event_result.first()
    await db.commit()

    async def originate(member):
        try:
            response = await call_station(member.sip_extension)
            return {"station_id": member.id, "station_number": member.station_number, "sip_extension": member.sip_extension, "status": "ORIGINATED", "response": response}
        except Exception as exc:
            return {"station_id": member.id, "station_number": member.station_number, "sip_extension": member.sip_extension, "status": "FAILED", "error": str(exc)}

    results = await asyncio.gather(*(originate(member) for member in members))
    originated = sum(1 for item in results if item["status"] == "ORIGINATED")
    failed = len(results) - originated
    status = "ORIGINATED" if originated else "FAILED"
    await db.execute(text("UPDATE emergency_events SET status=:status WHERE id=:id"), {"status": status, "id": event.id})
    await db.commit()

    return {
        "status": "EMERGENCY_ORIGINATED" if originated else "EMERGENCY_FAILED",
        "priority": EMERGENCY_PRIORITY,
        "event_id": event.id,
        "group_id": group.id,
        "group_code": group.code,
        "target_count": len(members),
        "originated_count": originated,
        "failed_count": failed,
        "results": results,
    }

@router.get("/events")
async def emergency_events(limit: int = 50, db: AsyncSession = Depends(get_db)):
    limit = max(1, min(limit, 200))
    result = await db.execute(text("""
        SELECT id,group_id,group_code,source_extension,priority,status,
               originated_at,acknowledged_at,ended_at,target_count,answered_count
        FROM emergency_events
        ORDER BY originated_at DESC
        LIMIT :limit
    """), {"limit": limit})
    return [dict(row._mapping) for row in result]

@router.post("/events/{event_id}/acknowledge")
async def acknowledge_emergency(event_id: int, db: AsyncSession = Depends(get_db)):
    now = datetime.now(timezone.utc)
    result = await db.execute(text("""
        UPDATE emergency_events
        SET acknowledged_at=COALESCE(acknowledged_at,:now), status=CASE WHEN status='ORIGINATED' THEN 'ACKNOWLEDGED' ELSE status END
        WHERE id=:id AND ended_at IS NULL
        RETURNING id,status,acknowledged_at
    """), {"id": event_id, "now": now})
    row = result.first()
    if row is None:
        raise HTTPException(status_code=404, detail="Active emergency event not found")
    await db.commit()
    return dict(row._mapping)
