from __future__ import annotations

import mimetypes
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from .master import require_admin

MONITOR_DIR = Path(os.getenv("TCCS_RECORDING_DIR", "/var/spool/asterisk/monitor")).resolve()
router = APIRouter(prefix="/recordings", tags=["recordings"])


def _recording_path(filename: str) -> Path:
    name = Path(filename).name
    if name != filename or not name.lower().endswith(".wav"):
        raise HTTPException(status_code=400, detail="Invalid recording filename")
    path = (MONITOR_DIR / name).resolve()
    if path.parent != MONITOR_DIR:
        raise HTTPException(status_code=400, detail="Invalid recording path")
    return path


def _recording_info(path: Path) -> dict:
    stat = path.stat()
    return {
        "filename": path.name,
        "size_bytes": stat.st_size,
        "modified_at": stat.st_mtime,
        "play_url": f"/api/v1/master/recordings/{path.name}/play",
        "download_url": f"/api/v1/master/recordings/{path.name}/download",
    }


@router.get("")
async def list_recordings(admin: dict = Depends(require_admin)):
    MONITOR_DIR.mkdir(parents=True, exist_ok=True)
    files = [p for p in MONITOR_DIR.iterdir() if p.is_file() and p.suffix.lower() == ".wav"]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [_recording_info(path) for path in files]


@router.get("/{filename}/play")
async def play_recording(filename: str, admin: dict = Depends(require_admin)):
    path = _recording_path(filename)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Recording not found")
    media_type = mimetypes.guess_type(path.name)[0] or "audio/wav"
    return FileResponse(
        path,
        media_type=media_type,
        filename=path.name,
        content_disposition_type="inline",
        headers={"Accept-Ranges": "bytes"},
    )


@router.get("/{filename}/download")
async def download_recording(filename: str, admin: dict = Depends(require_admin)):
    path = _recording_path(filename)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Recording not found")
    return FileResponse(
        path,
        media_type="audio/wav",
        filename=path.name,
        content_disposition_type="attachment",
    )


@router.delete("/{filename}")
async def delete_recording(filename: str, admin: dict = Depends(require_admin)):
    path = _recording_path(filename)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Recording not found")
    try:
        path.unlink()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Unable to delete recording: {exc}")
    return {"status": "DELETED", "filename": filename}
