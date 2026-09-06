from datetime import datetime, timezone
from uuid import uuid4

from fastapi import FastAPI, HTTPException

from .config import settings
from .db import check_database, close_database
from .models import CallRequest, CallState, CallStatus

app = FastAPI(title=settings.app_name, version=settings.app_version)


@app.on_event("shutdown")
async def shutdown() -> None:
    await close_database()


@app.get("/api/v1/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name, "version": settings.app_version}


@app.get("/api/v1/health/live")
async def liveness() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name}


@app.get("/api/v1/health/ready")
async def readiness() -> dict[str, str]:
    if not await check_database():
        raise HTTPException(status_code=503, detail="database unavailable")
    return {"status": "ready", "database": "ok", "service": settings.app_name}


@app.get("/api/v1/system/status")
async def system_status() -> dict[str, str]:
    database = "ok" if await check_database() else "unavailable"
    return {
        "service": settings.app_name,
        "environment": settings.environment,
        "time_utc": datetime.now(timezone.utc).isoformat(),
        "database": database,
    }


@app.post("/api/v1/calls", response_model=CallStatus)
async def create_call(request: CallRequest) -> CallStatus:
    if not request.source or not request.target:
        raise HTTPException(status_code=400, detail="source and target are required")

    return CallStatus(
        call_id=str(uuid4()),
        state=CallState.INITIATED,
        source=request.source,
        target=request.target,
    )


@app.get("/api/v1/calls/{call_id}", response_model=CallStatus)
async def get_call(call_id: str) -> CallStatus:
    raise HTTPException(status_code=404, detail=f"call {call_id} not found")
