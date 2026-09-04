from __future__ import annotations

import csv
import io
import mimetypes
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen import canvas
from sqlalchemy import text

from .db import SessionLocal
from .master import router as master_router
from .server_management import router as server_management_router
from .user_management import ROLE_ADMIN, ROLE_TESTROOM, require_user

MONITOR_DIR = Path(os.getenv("TCCS_RECORDING_DIR", "/var/spool/asterisk/monitor")).resolve()
router = APIRouter(prefix="/recordings", tags=["recordings"])

# server_management.py is also used as a standalone router, but here it is
# mounted inside the /api/v1/master router. Strip its absolute prefix before
# including it so the public API remains /api/v1/master/servers.
server_management_router.prefix = "/servers"
master_router.include_router(server_management_router)


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
    return {"filename": path.name, "size_bytes": stat.st_size, "modified_at": stat.st_mtime,
            "play_url": f"/api/v1/master/recordings/{path.name}/play",
            "download_url": f"/api/v1/master/recordings/{path.name}/download"}


def _can_manage(user: dict) -> bool:
    return user.get("role") in {ROLE_TESTROOM, ROLE_ADMIN}


def _history_filters(call_type: Optional[str], status: Optional[str], date_from: Optional[str], date_to: Optional[str]):
    clauses = []
    params: dict = {}
    if call_type and call_type.upper() != "ALL":
        ct = call_type.upper()
        if ct == "MISSED":
            clauses.append("status = 'MISSED'")
        elif ct in {"INCOMING", "DIRECT", "GROUP", "SECTION", "GENERAL"}:
            clauses.append("call_type = :call_type")
            params["call_type"] = ct
        else:
            raise HTTPException(status_code=400, detail="Invalid call type filter")
    if status and status.upper() != "ALL":
        st = status.upper()
        if st not in {"ORIGINATED", "RINGING", "ANSWERED", "ENDED", "MISSED"}:
            raise HTTPException(status_code=400, detail="Invalid status filter")
        clauses.append("status = :status")
        params["status"] = st
    if date_from:
        try:
            params["date_from"] = datetime.fromisoformat(date_from.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date_from; use ISO date/time")
        clauses.append("originated_at >= :date_from")
    if date_to:
        try:
            params["date_to"] = datetime.fromisoformat(date_to.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date_to; use ISO date/time")
        clauses.append("originated_at <= :date_to")
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


async def _history_rows(call_type: Optional[str], status: Optional[str], date_from: Optional[str], date_to: Optional[str], limit: int = 500):
    where, params = _history_filters(call_type, status, date_from, date_to)
    params["limit"] = max(1, min(limit, 5000))
    async with SessionLocal() as db:
        result = await db.execute(text(f"""
            SELECT id, call_type, source_extension, target_station_number, target_name,
                   group_code, status, originated_at, answered_at, ended_at, duration_seconds
            FROM call_history {where} ORDER BY originated_at DESC LIMIT :limit
        """), params)
        return [dict(row._mapping) for row in result]


def _serialize(row: dict) -> dict:
    def dt(value):
        return value.isoformat() if hasattr(value, "isoformat") else value
    return {**row, "originated_at": dt(row.get("originated_at")), "answered_at": dt(row.get("answered_at")), "ended_at": dt(row.get("ended_at"))}


@router.get("/history")
async def recording_history(call_type: Optional[str] = Query(None), status: Optional[str] = Query(None), date_from: Optional[str] = Query(None), date_to: Optional[str] = Query(None), limit: int = Query(500, ge=1, le=5000), user: dict = Depends(require_user)):
    if not _can_manage(user):
        raise HTTPException(status_code=403, detail="Recording access is restricted to Testroom and Administrator users")
    return [_serialize(row) for row in await _history_rows(call_type, status, date_from, date_to, limit)]


@router.get("/history/export.csv")
async def export_history_csv(call_type: Optional[str] = Query(None), status: Optional[str] = Query(None), date_from: Optional[str] = Query(None), date_to: Optional[str] = Query(None), user: dict = Depends(require_user)):
    if not _can_manage(user):
        raise HTTPException(status_code=403, detail="Recording access is restricted to Testroom and Administrator users")
    rows = [_serialize(row) for row in await _history_rows(call_type, status, date_from, date_to, 5000)]
    fields = ["id", "call_type", "source_extension", "target_station_number", "target_name", "group_code", "status", "originated_at", "answered_at", "ended_at", "duration_seconds"]
    stream = io.StringIO(); writer = csv.writer(stream); writer.writerow(fields)
    for row in rows: writer.writerow([row.get(field, "") for field in fields])
    data = io.BytesIO(stream.getvalue().encode("utf-8-sig"))
    return StreamingResponse(data, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=tccs-call-history.csv"})


@router.get("/history/export.pdf")
async def export_history_pdf(call_type: Optional[str] = Query(None), status: Optional[str] = Query(None), date_from: Optional[str] = Query(None), date_to: Optional[str] = Query(None), user: dict = Depends(require_user)):
    if not _can_manage(user):
        raise HTTPException(status_code=403, detail="Recording access is restricted to Testroom and Administrator users")
    rows = [_serialize(row) for row in await _history_rows(call_type, status, date_from, date_to, 5000)]
    output = io.BytesIO(); page = landscape(A4); pdf = canvas.Canvas(output, pagesize=page); width, height = page
    headers = ["TIME", "TYPE", "SOURCE", "TARGET", "STATUS", "ANSWERED", "ENDED", "DURATION"]; x = [24, 145, 225, 295, 430, 510, 590, 680]
    def header():
        pdf.setFont("Helvetica-Bold", 10); pdf.drawString(24, height - 28, "TCCS CALL HISTORY REPORT")
        pdf.setFont("Helvetica", 7); filters = f"Type={call_type or 'ALL'}  Status={status or 'ALL'}  From={date_from or '—'}  To={date_to or '—'}"; pdf.drawString(24, height - 42, filters[:180])
        pdf.setFont("Helvetica-Bold", 7)
        for i, h in enumerate(headers): pdf.drawString(x[i], height - 62, h)
    header(); y = height - 75; pdf.setFont("Helvetica", 6.5)
    for row in rows:
        if y < 28: pdf.showPage(); header(); y = height - 75; pdf.setFont("Helvetica", 6.5)
        target = f"{row.get('target_station_number') or ''} {row.get('target_name') or ''}".strip()
        values = [str(row.get("originated_at") or "")[:19].replace("T", " "), row.get("call_type") or "", row.get("source_extension") or "", target[:25], row.get("status") or "", str(row.get("answered_at") or "")[:19].replace("T", " "), str(row.get("ended_at") or "")[:19].replace("T", " "), f"{int(row['duration_seconds'])}s" if row.get("duration_seconds") is not None else "—"]
        for i, value in enumerate(values): pdf.drawString(x[i], y, str(value)[:26])
        y -= 12
    if not rows: pdf.drawString(24, y, "No call history records match the selected filters.")
    pdf.save(); output.seek(0)
    return StreamingResponse(output, media_type="application/pdf", headers={"Content-Disposition": "attachment; filename=tccs-call-history.pdf"})


@router.get("")
async def list_recordings(user: dict = Depends(require_user)):
    if not _can_manage(user): raise HTTPException(status_code=403, detail="Recording access is restricted to Testroom and Administrator users")
    MONITOR_DIR.mkdir(parents=True, exist_ok=True); files = [p for p in MONITOR_DIR.iterdir() if p.is_file() and p.suffix.lower() == ".wav"]; files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [_recording_info(path) for path in files]


@router.get("/{filename}/play")
async def play_recording(filename: str, user: dict = Depends(require_user)):
    if not _can_manage(user): raise HTTPException(status_code=403, detail="Recording access is restricted to Testroom and Administrator users")
    path = _recording_path(filename)
    if not path.is_file(): raise HTTPException(status_code=404, detail="Recording not found")
    media_type = mimetypes.guess_type(path.name)[0] or "audio/wav"
    return FileResponse(path, media_type=media_type, headers={"Accept-Ranges": "bytes", "Cache-Control": "no-store", "Content-Disposition": f'inline; filename="{path.name}"'})


@router.get("/{filename}/download")
async def download_recording(filename: str, user: dict = Depends(require_user)):
    if not _can_manage(user): raise HTTPException(status_code=403, detail="Recording access is restricted to Testroom and Administrator users")
    path = _recording_path(filename)
    if not path.is_file(): raise HTTPException(status_code=404, detail="Recording not found")
    return FileResponse(path, media_type="audio/wav", headers={"Cache-Control": "no-store", "Content-Disposition": f'attachment; filename="{path.name}"'})


@router.delete("/{filename}")
async def delete_recording(filename: str, user: dict = Depends(require_user)):
    if user.get("role") != ROLE_ADMIN: raise HTTPException(status_code=403, detail="Only Administrator users can delete recordings")
    path = _recording_path(filename)
    if not path.is_file(): raise HTTPException(status_code=404, detail="Recording not found")
    try: path.unlink()
    except OSError as exc: raise HTTPException(status_code=500, detail=f"Unable to delete recording: {exc}")
    return {"status": "DELETED", "filename": filename}
