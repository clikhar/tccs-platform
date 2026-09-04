from __future__ import annotations

import asyncio
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Set

from sqlalchemy import text

from .asterisk import active_channel_details
from .ami import enforce_single_conference_channel
from .db import SessionLocal
from .master import router as master_router
from .recording_management import router as recording_management_router
from .user_management import ensure_user_schema, router as user_management_router

master_router.include_router(recording_management_router)
master_router.include_router(user_management_router)

LEGACY_CONFERENCE = os.getenv("TCCS_RECORDING_CONFERENCE", "").strip()
POLL_INTERVAL = float(os.getenv("TCCS_RECORDING_POLL_INTERVAL", "1.0"))
MONITOR_DIR = os.getenv("TCCS_RECORDING_DIR", "/var/spool/asterisk/monitor")
ASTERISK_CLI = os.getenv("ASTERISK_CLI", "/usr/sbin/asterisk")
HISTORY_ORIGINATE_TIMEOUT = int(os.getenv("TCCS_HISTORY_ORIGINATE_TIMEOUT", "45"))
CONTROLLER_CONFERENCE_PREFIX = "TCCS-CTRL-"


def _conference_from_channel(channel: Dict[str, str]) -> str | None:
    if channel.get("context") not in {"tccs-stations", "tccs-controller"} or channel.get("application") != "CONFBRIDGE":
        return None
    conference = channel.get("data", "").split(",", 1)[0].strip()
    if not conference:
        return None
    if conference.upper().startswith(CONTROLLER_CONFERENCE_PREFIX) or conference == LEGACY_CONFERENCE:
        return conference
    return None


def _controller_conferences(channels: List[Dict[str, str]]) -> Set[str]:
    conferences = {name for channel in channels if (name := _conference_from_channel(channel))}
    if LEGACY_CONFERENCE:
        conferences.add(LEGACY_CONFERENCE)
    return conferences


def _station_channels(channels: List[Dict[str, str]], conference: str) -> List[Dict[str, str]]:
    wanted = conference.strip().lower()
    return [c for c in channels if _conference_from_channel(c)
            and c.get("data", "").split(",", 1)[0].strip().lower() == wanted
            and re.fullmatch(r"10\d{2}", c.get("extension", "").strip())]


def _outbound_station_channels(channels: List[Dict[str, str]]) -> List[Dict[str, str]]:
    return [c for c in channels if c.get("context") == "tccs-stations"
            and c.get("dialed_extension") == "900"
            and re.fullmatch(r"10\d{2}", c.get("extension", "").strip())]


def _incoming_controller_channels(channels: List[Dict[str, str]]) -> List[Dict[str, str]]:
    return [c for c in channels if c.get("context") == "tccs-stations"
            and c.get("dialed_extension") == "9999"
            and re.fullmatch(r"10\d{2}", c.get("extension", "").strip())]


def _record_filename(conference: str) -> str:
    now = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    safe_conference = re.sub(r"[^A-Za-z0-9_.-]", "_", conference)
    return os.path.join(MONITOR_DIR, f"tccs-{safe_conference.lower()}-{now}-{uuid.uuid4().hex[:8]}.wav")


async def _cli(command: str) -> str:
    process = await asyncio.create_subprocess_exec(ASTERISK_CLI, "-rx", command,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=10)
    if process.returncode != 0:
        raise RuntimeError(stderr.decode(errors="replace").strip() or f"Asterisk command failed: {command}")
    return stdout.decode(errors="replace").strip()


async def _finish_history(db, row, now: datetime) -> None:
    status = "ANSWERED" if row.answered_at is not None else "MISSED"
    await db.execute(text("""
        UPDATE call_history
        SET status=:status, ended_at=:now,
            duration_seconds=GREATEST(0, EXTRACT(EPOCH FROM (:now - COALESCE(answered_at, originated_at)))::INTEGER)
        WHERE id=:id AND ended_at IS NULL
    """), {"status": status, "now": now, "id": row.id})


async def _sync_outbound_call_history(channels: List[Dict[str, str]]) -> None:
    active_by_extension: Dict[str, Dict[str, str]] = {}
    for channel in _outbound_station_channels(channels):
        extension = channel.get("extension", "").strip()
        if extension and extension not in active_by_extension:
            active_by_extension[extension] = channel
    now = datetime.now(timezone.utc)
    async with SessionLocal() as db:
        result = await db.execute(text("""
            SELECT h.id,h.asterisk_channel,h.originated_at,h.answered_at,s.sip_extension
            FROM call_history h LEFT JOIN stations s ON s.id=h.target_station_id
            WHERE h.source_extension='9999' AND h.ended_at IS NULL ORDER BY h.originated_at
        """))
        for row in list(result):
            channel = active_by_extension.get((row.sip_extension or "").strip())
            if channel:
                channel_name = channel.get("channel", "").strip()
                if not channel_name:
                    continue
                application = channel.get("application", "").strip().upper()
                if row.asterisk_channel and row.asterisk_channel != channel_name:
                    await _finish_history(db, row, now)
                    continue
                status = "ANSWERED" if application == "CONFBRIDGE" else "RINGING"
                await db.execute(text("""
                    UPDATE call_history SET asterisk_channel=:channel,status=:status,
                    answered_at=CASE WHEN :status='ANSWERED' THEN COALESCE(answered_at,:now) ELSE answered_at END
                    WHERE id=:id AND ended_at IS NULL
                """), {"channel": channel_name, "status": status, "now": now, "id": row.id})
            elif row.asterisk_channel or (now - row.originated_at).total_seconds() >= HISTORY_ORIGINATE_TIMEOUT:
                await _finish_history(db, row, now)
        await db.commit()


async def _sync_incoming_call_history(channels: List[Dict[str, str]]) -> None:
    incoming = _incoming_controller_channels(channels)
    active_channels = {c.get("channel", "").strip() for c in incoming if c.get("channel")}
    now = datetime.now(timezone.utc)
    async with SessionLocal() as db:
        open_result = await db.execute(text("""
            SELECT id,asterisk_channel,originated_at,answered_at
            FROM call_history WHERE call_type='INCOMING' AND ended_at IS NULL
        """))
        for row in list(open_result):
            if not row.asterisk_channel or row.asterisk_channel not in active_channels:
                await _finish_history(db, row, now)

        for channel in incoming:
            source = channel.get("extension", "").strip()
            channel_name = channel.get("channel", "").strip()
            if not source or not channel_name:
                continue
            station_result = await db.execute(text("""
                SELECT id FROM stations WHERE sip_extension=:extension AND enabled=TRUE LIMIT 1
            """), {"extension": source})
            station = station_result.first()
            if station is None:
                continue
            answered = channel.get("application", "").strip().upper() == "CONFBRIDGE"
            existing = await db.execute(text("""
                SELECT id FROM call_history
                WHERE call_type='INCOMING' AND asterisk_channel=:channel AND ended_at IS NULL LIMIT 1
            """), {"channel": channel_name})
            row = existing.first()
            if row:
                await db.execute(text("""
                    UPDATE call_history SET status=CASE WHEN :answered THEN 'ANSWERED' ELSE 'RINGING' END,
                    answered_at=CASE WHEN :answered THEN COALESCE(answered_at,:now) ELSE answered_at END
                    WHERE id=:id AND ended_at IS NULL
                """), {"answered": answered, "now": now, "id": row.id})
            else:
                await db.execute(text("""
                    INSERT INTO call_history
                    (call_type,source_extension,target_station_id,target_station_number,target_name,
                     status,originated_at,answered_at,asterisk_channel)
                    VALUES ('INCOMING',:source,:station_id,'CTRL-9999','TCCS CONTROLLER',
                            :status,:now,:answered_at,:channel)
                """), {"source": source, "station_id": station.id,
                       "status": "ANSWERED" if answered else "RINGING",
                       "now": now, "answered_at": now if answered else None, "channel": channel_name})
        await db.commit()


async def _is_recording(conference: str) -> bool:
    output = await _cli("core show channels concise")
    wanted = conference.strip().lower()
    for line in output.splitlines():
        fields = line.strip().split("!")
        if len(fields) < 7:
            continue
        channel = fields[0].strip().lower()
        if (channel.startswith(f"confbridgerecorder/conf-{wanted}-")
                or channel.startswith(f"confbridgerecorder/{wanted}-")
                or channel.startswith(f"cbrecord/{wanted}-")):
            return True
    return False


async def _start_recording(conference: str) -> None:
    output = await _cli(f"confbridge record start {conference} {_record_filename(conference)}")
    if "recording started" not in output.lower():
        raise RuntimeError(output or f"Asterisk did not start recording {conference}")


async def _stop_recording(conference: str) -> None:
    output = await _cli(f"confbridge record stop {conference}")
    if "recording stopped" not in output.lower():
        raise RuntimeError(output or f"Asterisk did not stop recording {conference}")


async def _enforce_controller_sessions(channels: List[Dict[str, str]]) -> None:
    controllers: Set[tuple[str, str]] = set()
    for channel in channels:
        if channel.get("context") != "tccs-controller" or channel.get("application") != "CONFBRIDGE":
            continue
        extension = channel.get("extension", "").strip()
        conference = channel.get("data", "").split(",", 1)[0].strip()
        if re.fullmatch(r"9\d{3}", extension) and conference:
            controllers.add((extension, conference))
    if any(c.get("extension", "").strip() == "9999" and c.get("context") == "tccs-controller" for c in channels):
        controllers.add(("9999", "SECTION01"))
    for extension, conference in controllers:
        try:
            await enforce_single_conference_channel(extension, conference)
        except Exception:
            continue


async def recording_loop() -> None:
    os.makedirs(MONITOR_DIR, exist_ok=True)
    async with SessionLocal() as db:
        await ensure_user_schema(db)
    while True:
        try:
            channels = await active_channel_details()
            await _enforce_controller_sessions(channels)
            await _sync_outbound_call_history(channels)
            await _sync_incoming_call_history(channels)
            for conference in _controller_conferences(channels):
                stations = _station_channels(channels, conference)
                recording = await _is_recording(conference)
                if stations and not recording:
                    await _start_recording(conference)
                elif not stations and recording:
                    await _stop_recording(conference)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        await asyncio.sleep(POLL_INTERVAL)
