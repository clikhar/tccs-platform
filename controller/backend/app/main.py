from fastapi import Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_db
from .models import Section, Station
from .schemas import SectionOut, StationOut

app = FastAPI(title="TCCS Controller API", version="0.3.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "tccs-controller-api"}


@app.get("/api/v1/sections", response_model=list[SectionOut])
async def list_sections(db: AsyncSession = Depends(get_db)) -> list[Section]:
    result = await db.scalars(select(Section).where(Section.enabled.is_(True)).order_by(Section.code))
    return list(result.all())


@app.get("/api/v1/stations", response_model=list[StationOut])
async def list_stations(
    section_id: int | None = None,
    db: AsyncSession = Depends(get_db),
) -> list[Station]:
    query = select(Station).where(Station.enabled.is_(True))
    if section_id is not None:
        query = query.where(Station.section_id == section_id)
    result = await db.scalars(query.order_by(Station.priority, Station.station_number))
    return list(result.all())


@app.get("/api/v1/stations/{station_id}", response_model=StationOut)
async def get_station(station_id: int, db: AsyncSession = Depends(get_db)) -> Station:
    station = await db.get(Station, station_id)
    if station is None or not station.enabled:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Station not found")
    return station
