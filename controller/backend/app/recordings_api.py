from __future__ import annotations

import mimetypes
import os
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from .master import require_admin

router = APIRouter(prefix="/api/v1/master/recordings", tags=["recordings"])

RECORDING_DIR = Path(os.getenv("TCCS_RECORDING_DIR", "/var/spool/asterisk/monitor")).resolve()
ALLOWED_EXTENSIONS = {".wav", ".wave", ".wav49", ".gsm", ".mp3", ".ogg", ".opus", ".m4a"}


def _recording_path(filename: str) -> Path:
    name = Path(filename).name
    if name != filename or not name or name in {".", ".."}:
        raise HTTPException(status_code=400, detail="Invalid recording filename")
    path = (RECORDING_DIR / name).resolve()
    try:
        path.relative_to(RECORDING_DIR)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid recording filename")
    if path.suffix.lower() not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Unsupported recording format")
    return path


def _recording_files() -> List[Path]:
    if not RECORDING_DIR.is_dir():
        return []
    return sorted(
        (p for p in RECORDING_DIR.iterdir() if p.is_file() and p.suffix.lower() in ALLOWED_EXTENSIONS),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


@router.get("")
async def list_recordings(admin: dict = Depends(require_admin)):
    records = []
    for path in _recording_files():
        try:
            stat = path.stat()
        except OSError:
            continue
        records.append(
            {
                "filename": path.name,
                "size_bytes": stat.st_size,
                "modified_at": stat.st_mtime,
            }
        )
    return records


@router.get("/{filename}/play")
async def play_recording(filename: str, admin: dict = Depends(require_admin)):
    path = _recording_path(filename)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Recording not found")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(
        path,
        media_type=media_type,
        filename=path.name,
        content_disposition_type="inline",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/{filename}/download")
async def download_recording(filename: str, admin: dict = Depends(require_admin)):
    path = _recording_path(filename)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Recording not found")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(
        path,
        media_type=media_type,
        filename=path.name,
        content_disposition_type="attachment",
        headers={"Cache-Control": "no-store"},
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
