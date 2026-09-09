from __future__ import annotations

import re

from .ami import conference_channels, enforce_single_conference_channel, originate_to_conference
from .asterisk import active_channel_details
from .core_client import core_client
from .db import SessionLocal
from sqlalchemy import text

DEFAULT_TCCS_CONFERENCE = "SECTION01"


def controller_conference(controller_extension: str | None) -> str:
    extension = str(controller_extension or "").strip()
    if not extension:
        raise ValueError("No enabled controller SIP account is assigned to this station's section")
    return f"TCCS-CTRL-{extension}"


async def controller_extension_for_station(extension: str) -> str:
    """Resolve the enabled controller SIP extension for a station's section."""
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
    if row is None:
        raise ValueError("No enabled controller SIP account is assigned to this station's section")
    return str(row.extension).strip()


async def conference_for_station(extension: str) -> str:
    """Resolve the controller-specific bridge for a station's section."""
    return controller_conference(await controller_extension_for_station(extension))


def _active_station_channels(channels: list[dict[str, str]], extension: str) -> list[dict[str, str]]:
    prefix = f"PJSIP/{str(extension).strip()}-"
    return [
        channel for channel in channels
        if channel.get("channel", "").startswith(prefix)
        and channel.get("context") in {"tccs-stations", "tccs-controller"}
    ]


async def _reject_duplicate_station_call(extension: str) -> None:
    """Reject a new call when the station already has a live SIP channel."""
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

    # Stage 1 Core owns call origination and media control. The browser's
    # registered controller endpoint is used as the source leg; the frontend
    # answers the incoming INVITE and Core then bridges the station to it.
    if core_client.enabled:
        source = await controller_extension_for_station(extension)
        return await core_client.create_call(
            source=source,
            target=extension,
            section_id=None,
            mode="individual",
        )

    # Legacy controller/Asterisk path retained for staged deployments until
    # TCCS_CORE_URL is configured.
    lock_key = abs(hash(extension)) % (2**31)
    async with SessionLocal() as db:
        await db.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key})
        await _reject_duplicate_station_call(extension)
        target_conference = conference or await conference_for_station(extension)
        await enforce_single_conference_channel(extension, target_conference)
        response = await originate_to_conference(extension, target_conference)
        await db.commit()
        return response


async def call_stations(
    extensions: list[str],
    *,
    mode: str = "group",
    group_code: str | None = None,
) -> dict:
    """Originate one controller source leg and attach multiple stations via Core."""
    normalized = []
    for extension in extensions:
        value = str(extension).strip()
        if not re.fullmatch(r"10\d{2}", value):
            raise ValueError(f"Invalid station SIP extension: {value}")
        if value not in normalized:
            normalized.append(value)
    if not normalized:
        raise ValueError("At least one station is required")
    if not core_client.enabled:
        raise RuntimeError("TCCS Core integration is required for multi-station calls")

    source = await controller_extension_for_station(normalized[0])
    return await core_client.create_conference(
        source=source,
        targets=normalized,
        section_id=None,
        mode=mode,
    )
