from fastapi import APIRouter, HTTPException

from ..models import CallRequest, CallStatus
from ..services import initiate_call

router = APIRouter(prefix="/api/v1")


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/calls", response_model=CallStatus)
async def create_call(request: CallRequest) -> CallStatus:
    if not request.source or not request.target:
        raise HTTPException(status_code=400, detail="source and target are required")
    return initiate_call(request)
