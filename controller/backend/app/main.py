from __future__ import annotations

import os
from typing import List, Optional

from fastapi import Body, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_db
from .models import Section, Station
from .schemas import SectionOut, StationOut

app = FastAPI(title="TCCS Controller API", version="0.3.4")

allowed_origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]
frontend_origin = os.getenv("TCCS_FRONTEND_ORIGIN")
if frontend_origin:
    allowed_origins.append(frontend_origin.rstrip("/"))

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "PUT", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok", "service": "tccs-controller-api"}


@app.get("/api/v1/sections", response_model=List[SectionOut])
async def list_sections(db: AsyncSession = Depends(get_db)) -> List[Section]:
    result = await db.scalars(
        select(Section).where(Section.enabled.is_(True)).order_by(Section.code)
    )
    return list(result.all())


@app.get("/api/v1/stations", response_model=List[StationOut])
async def list_stations(
    section_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
) -> List[Station]:
    query = select(Station).where(Station.enabled.is_(True))
    if section_id is not None:
        query = query.where(Station.section_id == section_id)
    result = await db.scalars(
        query.order_by(Station.priority, Station.station_number)
    )
    return list(result.all())


@app.put("/api/v1/stations/order")
async def save_station_order(
    station_ids: List[int] = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
):
    if not station_ids:
        raise HTTPException(status_code=400, detail="station_ids must not be empty")
    if len(station_ids) != len(set(station_ids)):
        raise HTTPException(status_code=400, detail="station_ids must be unique")

    stations = list(
        (
            await db.scalars(
                select(Station).where(
                    Station.id.in_(station_ids), Station.enabled.is_(True)
                )
            )
        ).all()
    )
    found_ids = {station.id for station in stations}
    if found_ids != set(station_ids):
        raise HTTPException(status_code=400, detail="One or more stations are invalid or disabled")

    for index, station_id in enumerate(station_ids, start=1):
        station = next(station for station in stations if station.id == station_id)
        station.priority = index * 10

    await db.commit()
    return {"status": "ok", "station_ids": station_ids}


@app.get("/api/v1/stations/{station_id}", response_model=StationOut)
async def get_station(
    station_id: int,
    db: AsyncSession = Depends(get_db),
) -> Station:
    station = await db.get(Station, station_id)
    if station is None or not station.enabled:
        raise HTTPException(status_code=404, detail="Station not found")
    return station
