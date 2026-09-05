from datetime import datetime, timezone
from uuid import uuid4

from fastapi import FastAPI, HTTPException

from .config import settings
from .models import CallRequest, CallState, CallStatus

app = FastAPI(title=settings.app_name, version=settings.app_version)


@app.get("/api/v1/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name, "version": settings.app_version}


@app.get("/api/v1/system/status")
async def system_status() -> dict[str, str]:
    return {
        "service": settings.app_name,
        "environment": settings.environment,
        "time_utc": datetime.now(timezone.utc).isoformat(),
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
    # Stage 1.1 placeholder. Persistent call state is introduced with the
    # database-backed call/event service in the next implementation phase.
    raise HTTPException(status_code=404, detail=f"call {call_id} not found")
