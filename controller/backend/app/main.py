from fastapi import FastAPI, HTTPException

from .data import STATIONS

app = FastAPI(title="TCCS Controller API", version="0.2.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "tccs-controller-api"}


@app.get("/api/v1/sections")
def list_sections() -> list[dict[str, object]]:
    return [{"id": 1, "number": "01", "name": "SECTION 01"}]


@app.get("/api/v1/stations")
def list_stations(section_id: int | None = None) -> list[dict[str, object]]:
    stations = STATIONS if section_id is None else [s for s in STATIONS if s.section_id == section_id]
    return [station.__dict__ for station in stations]


@app.get("/api/v1/stations/{station_id}")
def get_station(station_id: int) -> dict[str, object]:
    for station in STATIONS:
        if station.id == station_id:
            return station.__dict__
    raise HTTPException(status_code=404, detail="Station not found")
