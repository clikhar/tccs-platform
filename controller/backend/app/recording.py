from __future__ import annotations

import asyncio
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Dict, List

from sqlalchemy import text

from .asterisk import active_channel_details
from .db import SessionLocal
from .master import router as master_router
from .recording_management import router as recording_management_router

master_router.include_router(recording_management_router)

CONFERENCE = os.getenv("TCCS_RECORDING_CONFERENCE", "SECTION01")
POLL_INTERVAL = float(os.getenv("TCCS_RECORDING_POLL_INTERVAL", "1.0"))
MONITOR_DIR = os.getenv("TCCS_RECORDING_DIR", "/var/spool/asterisk/monitor")
ASTERISK_CLI = os.getenv("ASTERISK_CLI", "/usr/sbin/asterisk")
HISTORY_ORIGINATE_TIMEOUT = int(os.getenv("TCCS_HISTORY_ORIGINATE_TIMEOUT", "45"))


def _station_channels(channels: List[Dict[str, str]]) -> List[Dict[str, str]]:
    conference = CONFERENCE.strip().lower()
    result = []
    for channel in channels:
        if channel.get("context") != "tccs-stations":
            continue
        if channel.get("application") != "CONFBRIDGE":
            continue
        if channel.get("data", "").split(",", 1)[0].strip().lower() != conference:
            continue
        if not re.fullmatch(r"10\d{2}", channel.get("extension", "").strip()):
            continue
        result.append(channel)
    return result


def _outbound_station_channels(channels: List[Dict[str, str]]) -> List[Dict[str, str]]:
    result = []
    for channel in channels:
        if channel.get("context") != "tccs-stations":
            continue
        if channel.get("dialed_extension") != "900":
            continue
        if not re.fullmatch(r"10\d{2}", channel.get("extension", "").strip()):
            continue
        result.append(channel)
    return result


def _record_filename() -> str:
    now = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return os.path.join(MONITOR_DIR, f"tccs-{CONFERENCE.lower()}-{now}-{uuid.uuid4().hex[:8]}.wav")


async def _cli(command: str) -> str:
    process = await asyncio.create_subprocess_exec(
        ASTERISK_CLI, "-rx", command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=10)
    if process.returncode != 0:
        detail = stderr.decode(errors="replace").strip()
        raise RuntimeError(detail or f"Asterisk command failed: {command}")
    return stdout.decode(errors="replace").strip()


async def _sync_outbound_call_history(channels: List[Dict[str, str]]) -> None:
    """Keep outbound history aligned with the real Asterisk station channel."""
    active_by_extension: Dict[str, Dict[str, str]] = {}
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
            WHERE h.source_extension='9999' AND h.ended_at IS NULL
            ORDER BY h.originated_at
        """))
        rows = list(result)
        for row in rows:
            extension = (row.sip_extension or "").strip()
            channel = active_by_extension.get(extension)
            if channel:
                channel_name = channel.get("channel", "").strip()
                application = channel.get("application", "").strip().upper()
                if not channel_name:
                    continue
                if row.asterisk_channel and row.asterisk_channel != channel_name:
                    await db.execute(text("""
                        UPDATE call_history SET status='ENDED',ended_at=:now,
                        duration_seconds=GREATEST(0,EXTRACT(EPOCH FROM (:now-COALESCE(answered_at,originated_at)))::INTEGER)
                        WHERE id=:id
                    """), {"now":now,"id":row.id})
                    continue
                if application == "CONFBRIDGE":
                    await db.execute(text("""
                        UPDATE call_history SET asterisk_channel=:channel,status='ANSWERED',answered_at=COALESCE(answered_at,:now)
                        WHERE id=:id AND ended_at IS NULL
                    """), {"channel":channel_name,"now":now,"id":row.id})
                else:
                    await db.execute(text("""
                        UPDATE call_history SET asterisk_channel=:channel,status=CASE WHEN status='ORIGINATED' THEN 'RINGING' ELSE status END
                        WHERE id=:id AND ended_at IS NULL
                    """), {"channel":channel_name,"id":row.id})
                continue
            age=(now-row.originated_at).total_seconds()
            if row.asterisk_channel or age>=HISTORY_ORIGINATE_TIMEOUT:
                await db.execute(text("""
                    UPDATE call_history SET status='ENDED',ended_at=:now,
                    duration_seconds=GREATEST(0,EXTRACT(EPOCH FROM (:now-COALESCE(answered_at,originated_at)))::INTEGER)
                    WHERE id=:id AND ended_at IS NULL
                """), {"now":now,"id":row.id})
        await db.commit()


async def _is_recording() -> bool:
    output = await _cli("core show channels concise")
    prefix = f"CBRec/{CONFERENCE}-".lower()
    return any(line.strip().split("!",1)[0].lower().startswith(prefix) for line in output.splitlines())


async def _start_recording() -> None:
    filename = _record_filename()
    output = await _cli(f"confbridge record start {CONFERENCE} {filename}")
    if "recording started" not in output.lower():
        raise RuntimeError(output or "Asterisk did not start conference recording")


async def _stop_recording() -> None:
    output = await _cli(f"confbridge record stop {CONFERENCE}")
    if "recording stopped" not in output.lower():
        raise RuntimeError(output or "Asterisk did not stop conference recording")


async def recording_loop() -> None:
    """Record SECTION01 and continuously synchronize call history."""
    os.makedirs(MONITOR_DIR, exist_ok=True)
    while True:
        try:
            channels = await active_channel_details()
            await _sync_outbound_call_history(channels)
            stations = _station_channels(channels)
            recording = await _is_recording()
            if stations and not recording:
                await _start_recording()
            elif not stations and recording:
                await _stop_recording()
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        await asyncio.sleep(POLL_INTERVAL)
