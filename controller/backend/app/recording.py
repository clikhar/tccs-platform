from __future__ import annotations

import asyncio
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Set

from sqlalchemy import text

from .asterisk import active_channel_details
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
    if channel.get("context") != "tccs-stations" or channel.get("application") != "CONFBRIDGE":
        return None
    conference = channel.get("data", "").split(",", 1)[0].strip()
    if not conference.upper().startswith(CONTROLLER_CONFERENCE_PREFIX):
        return None
    return conference


def _controller_conferences(channels: List[Dict[str, str]]) -> Set[str]:
    conferences = {name for channel in channels if (name := _conference_from_channel(channel))}
    if LEGACY_CONFERENCE:
        conferences.add(LEGACY_CONFERENCE)
    return conferences


def _station_channels(channels: List[Dict[str, str]], conference: str) -> List[Dict[str, str]]:
    wanted = conference.strip().lower()
    result = []
    for channel in channels:
        if _conference_from_channel(channel) and channel.get("data", "").split(",", 1)[0].strip().lower() == wanted:
            if re.fullmatch(r"10\d{2}", channel.get("extension", "").strip()):
                result.append(channel)
    return result


def _outbound_station_channels(channels: List[Dict[str, str]]) -> List[Dict[str, str]]:
    return [
        c for c in channels
        if c.get("context") == "tccs-stations"
        and c.get("dialed_extension") == "900"
        and re.fullmatch(r"10\d{2}", c.get("extension", "").strip())
    ]


def _record_filename(conference: str) -> str:
    now = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    safe_conference = re.sub(r"[^A-Za-z0-9_.-]", "_", conference)
    return os.path.join(MONITOR_DIR, f"tccs-{safe_conference.lower()}-{now}-{uuid.uuid4().hex[:8]}.wav")


async def _cli(command: str) -> str:
    process = await asyncio.create_subprocess_exec(
        ASTERISK_CLI, "-rx", command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=10)
    if process.returncode != 0:
        raise RuntimeError(stderr.decode(errors="replace").strip() or f"Asterisk command failed: {command}")
    return stdout.decode(errors="replace").strip()


async def _sync_outbound_call_history(channels: List[Dict[str, str]]) -> None:
    active_by_extension = {}
    for channel in _outbound_station_channels(channels):
        extension = channel.get("extension", "").strip()
        if extension and extension not in active_by_extension:
            active_by_extension[extension] = channel
    now = datetime.now(timezone.utc)
    async with SessionLocal() as db:
        result = await db.execute(text("""
            SELECT h.id,h.asterisk_channel,h.originated_at,h.answered_at,s.sip_extension
            FROM call_history h
            LEFT JOIN stations s ON s.id=h.target_station_id
            WHERE h.ended_at IS NULL
            ORDER BY h.originated_at
        """))
        for row in list(result):
            channel = active_by_extension.get((row.sip_extension or "").strip())
            if channel:
                channel_name = channel.get("channel", "").strip()
                application = channel.get("application", "").strip().upper()
                if not channel_name:
                    continue
                if row.asterisk_channel and row.asterisk_channel != channel_name:
                    await db.execute(text("UPDATE call_history SET status='ENDED',ended_at=:now,duration_seconds=GREATEST(0,EXTRACT(EPOCH FROM (:now-COALESCE(answered_at,originated_at)))::INTEGER) WHERE id=:id"), {"now": now, "id": row.id})
                    continue
                if application == "CONFBRIDGE":
                    await db.execute(text("UPDATE call_history SET asterisk_channel=:channel,status='ANSWERED',answered_at=COALESCE(answered_at,:now) WHERE id=:id AND ended_at IS NULL"), {"channel": channel_name, "now": now, "id": row.id})
                else:
                    await db.execute(text("UPDATE call_history SET asterisk_channel=:channel,status=CASE WHEN status='ORIGINATED' THEN 'RINGING' ELSE status END WHERE id=:id AND ended_at IS NULL"), {"channel": channel_name, "id": row.id})
                continue
            if row.asterisk_channel or (now - row.originated_at).total_seconds() >= HISTORY_ORIGINATE_TIMEOUT:
                await db.execute(text("UPDATE call_history SET status='ENDED',ended_at=:now,duration_seconds=GREATEST(0,EXTRACT(EPOCH FROM (:now-COALESCE(answered_at,originated_at)))::INTEGER) WHERE id=:id AND ended_at IS NULL"), {"now": now, "id": row.id})
        await db.commit()


async def _is_recording(conference: str) -> bool:
    output = await _cli("core show channels concise")
    prefix = f"CBRec/{conference}-".lower()
    return any(line.strip().split("!", 1)[0].lower().startswith(prefix) for line in output.splitlines())


async def _start_recording(conference: str) -> None:
    output = await _cli(f"confbridge record start {conference} {_record_filename(conference)}")
    if "recording started" not in output.lower():
        raise RuntimeError(output or f"Asterisk did not start recording {conference}")


async def _stop_recording(conference: str) -> None:
    output = await _cli(f"confbridge record stop {conference}")
    if "recording stopped" not in output.lower():
        raise RuntimeError(output or f"Asterisk did not stop recording {conference}")


async def recording_loop() -> None:
    os.makedirs(MONITOR_DIR, exist_ok=True)
    async with SessionLocal() as db:
        await ensure_user_schema(db)
    while True:
        try:
            channels = await active_channel_details()
            await _sync_outbound_call_history(channels)
            conferences = _controller_conferences(channels)
            for conference in conferences:
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
