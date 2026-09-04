from __future__ import annotations

import mimetypes
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from .user_management import ROLE_ADMIN, ROLE_TESTROOM, require_user

MONITOR_DIR = Path(os.getenv("TCCS_RECORDING_DIR", "/var/spool/asterisk/monitor")).resolve()
router = APIRouter(prefix="/recordings", tags=["recordings"])


def _recording_path(filename: str) -> Path:
    name=Path(filename).name
    if name!=filename or not name.lower().endswith(".wav"): raise HTTPException(status_code=400,detail="Invalid recording filename")
    path=(MONITOR_DIR/name).resolve()
    if path.parent!=MONITOR_DIR: raise HTTPException(status_code=400,detail="Invalid recording path")
    return path


def _recording_info(path: Path)->dict:
    stat=path.stat()
    return {"filename":path.name,"size_bytes":stat.st_size,"modified_at":stat.st_mtime,"play_url":f"/api/v1/master/recordings/{path.name}/play","download_url":f"/api/v1/master/recordings/{path.name}/download"}


def _can_manage(user:dict)->bool:
    return user.get("role") in {ROLE_TESTROOM,ROLE_ADMIN}


@router.get("")
async def list_recordings(user:dict=Depends(require_user)):
    if not _can_manage(user): raise HTTPException(status_code=403,detail="Recording access is restricted to Testroom and Administrator users")
    MONITOR_DIR.mkdir(parents=True,exist_ok=True); files=[p for p in MONITOR_DIR.iterdir() if p.is_file() and p.suffix.lower()==".wav"]; files.sort(key=lambda p:p.stat().st_mtime,reverse=True); return [_recording_info(path) for path in files]


@router.get("/{filename}/play")
async def play_recording(filename:str,user:dict=Depends(require_user)):
    if not _can_manage(user): raise HTTPException(status_code=403,detail="Recording access is restricted to Testroom and Administrator users")
    path=_recording_path(filename)
    if not path.is_file(): raise HTTPException(status_code=404,detail="Recording not found")
    media_type=mimetypes.guess_type(path.name)[0] or "audio/wav"
    return FileResponse(path,media_type=media_type,headers={"Accept-Ranges":"bytes","Cache-Control":"no-store","Content-Disposition":f'inline; filename="{path.name}"'})


@router.get("/{filename}/download")
async def download_recording(filename:str,user:dict=Depends(require_user)):
    if not _can_manage(user): raise HTTPException(status_code=403,detail="Recording access is restricted to Testroom and Administrator users")
    path=_recording_path(filename)
    if not path.is_file(): raise HTTPException(status_code=404,detail="Recording not found")
    return FileResponse(path,media_type="audio/wav",headers={"Cache-Control":"no-store","Content-Disposition":f'attachment; filename="{path.name}"'})


@router.delete("/{filename}")
async def delete_recording(filename:str,user:dict=Depends(require_user)):
    if user.get("role")!=ROLE_ADMIN: raise HTTPException(status_code=403,detail="Only Administrator users can delete recordings")
    path=_recording_path(filename)
    if not path.is_file(): raise HTTPException(status_code=404,detail="Recording not found")
    try: path.unlink()
    except OSError as exc: raise HTTPException(status_code=500,detail=f"Unable to delete recording: {exc}")
    return {"status":"DELETED","filename":filename}
