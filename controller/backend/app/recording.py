from __future__ import annotations

import asyncio
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Dict, List

from .asterisk import active_channel_details

CONFERENCE = os.getenv("TCCS_RECORDING_CONFERENCE", "SECTION01")
POLL_INTERVAL = float(os.getenv("TCCS_RECORDING_POLL_INTERVAL", "1.0"))
MONITOR_DIR = os.getenv("TCCS_RECORDING_DIR", "/var/spool/asterisk/monitor")
ASTERISK_CLI = os.getenv("ASTERISK_CLI", "/usr/sbin/asterisk")


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


async def _is_recording() -> bool:
    output = await _cli("core show channels concise")
    prefix = f"CBRec/{CONFERENCE}-".lower()
    return any(line.strip().split("!", 1)[0].lower().startswith(prefix) for line in output.splitlines())


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
    """Record SECTION01 only while at least one station is in the conference.

    The controller (9999) is deliberately ignored when deciding whether a
    recording should exist. A recording therefore represents an operational
    station conference session rather than the lifetime of the controller UI.
    """
    os.makedirs(MONITOR_DIR, exist_ok=True)
    while True:
        try:
            channels = await active_channel_details()
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
