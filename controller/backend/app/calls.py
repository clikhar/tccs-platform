from __future__ import annotations

import re

from .ami import conference_channels, enforce_single_conference_channel, originate_to_conference
from .asterisk import active_channel_details
from .db import SessionLocal
from sqlalchemy import text

DEFAULT_TCCS_CONFERENCE = "SECTION01"


def controller_conference(controller_extension: str | None) -> str:
    extension = str(controller_extension or "").strip()
    if not extension:
        raise ValueError("No enabled controller SIP account is assigned to this station's section")
    return f"TCCS-CTRL-{extension}"


async def conference_for_station(extension: str) -> str:
    """Resolve the controller-specific bridge for a station's section.

    A station belongs to one section. The section is associated with the
    controller, and that controller's SIP extension is the bridge namespace.
    A missing controller is an error rather than a fallback to a shared bridge;
    this prevents audio from one section/controller entering another bridge.
    """
    async with SessionLocal() as db:
        result = await db.execute(text("""
            SELECT sa.extension
            FROM stations st
            JOIN controllers c ON c.section_id=st.section_id AND c.enabled=TRUE
            JOIN sip_accounts sa ON sa.id=c.sip_account_id AND sa.enabled=TRUE
            WHERE st.sip_extension=:extension
            ORDER BY c.id
            LIMIT 1
        """), {"extension": str(extension).strip()})
        row = result.first()
    return controller_conference(row.extension if row else None)


def _active_station_channels(channels: list[dict[str, str]], extension: str) -> list[dict[str, str]]:
    prefix = f"PJSIP/{str(extension).strip()}-"
    return [
        channel for channel in channels
        if channel.get("channel", "").startswith(prefix)
        and channel.get("context") in {"tccs-stations", "tccs-controller"}
    ]


async def _reject_duplicate_station_call(extension: str) -> None:
    """Reject a new call when the station already has a live SIP channel.

    This is deliberately server-side. The browser's state can be stale after a
    refresh, reconnect, or network interruption, so Asterisk is the source of
    truth for whether the station is already engaged.
    """
    channels = await active_channel_details()
    active = _active_station_channels(channels, extension)
    if active:
        states = sorted({c.get("state", "UNKNOWN") for c in active})
        raise RuntimeError(
            f"Station {extension} already has an active SIP call ({', '.join(states)})"
        )


async def call_station(extension: str, conference: str | None = None):
    extension = str(extension).strip()
    if not re.fullmatch(r"10\d{2}", extension):
        raise ValueError("Invalid station SIP extension")

    # PostgreSQL advisory lock closes the race where two HTTP requests arrive
    # for the same station at almost exactly the same time.
    lock_key = abs(hash(extension)) % (2**31)
    async with SessionLocal() as db:
        await db.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key})
        await _reject_duplicate_station_call(extension)
        target_conference = conference or await conference_for_station(extension)

        # If Asterisk has retained duplicate conference channels from an older
        # browser/SIP session, clean them before originating another call.
        await enforce_single_conference_channel(extension, target_conference)
        response = await originate_to_conference(extension, target_conference)
        await db.commit()
        return response
