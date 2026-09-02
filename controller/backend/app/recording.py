from __future__ import annotations

import asyncio
import os
import re
import uuid
from datetime import datetime, timezone
from typing import List, Dict

from .asterisk import active_channel_details
from .ami import conference_is_recording, start_conference_recording, stop_conference_recording

CONFERENCE = os.getenv("TCCS_RECORDING_CONFERENCE", "SECTION01")
POLL_INTERVAL = float(os.getenv("TCCS_RECORDING_POLL_INTERVAL", "1.0"))
MONITOR_DIR = os.getenv("TCCS_RECORDING_DIR", "/var/spool/asterisk/monitor")


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


async def recording_loop() -> None:
    """Record SECTION01 only while at least one station is in the conference.

    The controller (9999) is deliberately ignored when deciding whether a
    recording should exist. This gives TCCS one recording per active station
    conference session instead of one long recording for the permanently
    connected controller.
    """
    os.makedirs(MONITOR_DIR, exist_ok=True)
    while True:
        try:
            channels = await active_channel_details()
            stations = _station_channels(channels)
            recording = await conference_is_recording(CONFERENCE)

            if stations and not recording:
                filename = _record_filename()
                await start_conference_recording(CONFERENCE, filename)
            elif not stations and recording:
                await stop_conference_recording(CONFERENCE)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Asterisk may be between bridge/channel transitions. Retry on the
            # next cycle instead of terminating the backend recording worker.
            pass
        await asyncio.sleep(POLL_INTERVAL)
