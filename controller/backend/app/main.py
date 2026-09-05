from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .asterisk import active_channel_details, endpoint_status
from .db import get_db
from .emergency import ensure_emergency_tables, router as emergency_router
from .master import ensure_master_tables, router as master_router
from .models import Section, Station
from .recording import start_recording_worker, stop_recording_worker
from .server_management import router as server_management_router
from .user_management import router as user_management_router

app = FastAPI(title="TCCS Controller API", version="0.5.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(master_router)
app.include_router(server_management_router)
app.include_router(emergency_router)
app.include_router(user_management_router)


class SectionOut(Any):
    pass


class StationOut(Any):
    pass


@app.on_event("startup")
async def startup() -> None:
    async with get_db() as db:
        await ensure_master_tables(db)
        await ensure_emergency_tables(db)
        await db.execute(text("""
            CREATE TABLE IF NOT EXISTS call_history (
                id BIGSERIAL PRIMARY KEY,
                call_type VARCHAR(32) NOT NULL,
                source_extension VARCHAR(32),
                target_station_id BIGINT,
                target_station_number VARCHAR(32),
                target_name VARCHAR(128),
                group_code VARCHAR(64),
                status VARCHAR(32) NOT NULL DEFAULT 'ORIGINATED',
                originated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                answered_at TIMESTAMPTZ,
                ended_at TIMESTAMPTZ,
                duration_seconds INTEGER,
                asterisk_channel VARCHAR(255)
            )
        """))
        await db.commit()
    app.state.recording_task = asyncio.create_task(start_recording_worker())


@app.on_event("shutdown")
async def shutdown() -> None:
    task = getattr(app.state, "recording_task", None)
    if task is not None:
        await stop_recording_worker(task)


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
async def asterisk_endpoints() -> List[Dict[str, Any]]:
    return await endpoint_status()


@app.get("/api/v1/asterisk/channels")
async def asterisk_channels() -> dict:
    channels = await active_channel_details()
    return {"channels": channels}


@app.post("/api/v1/calls/direct")
async def direct_call() -> dict:
    raise HTTPException(status_code=501, detail="Use the station call endpoint")
