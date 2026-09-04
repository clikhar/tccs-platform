from __future__ import annotations

import asyncio
import os
import re
from typing import Any, Dict, List

ASTERISK_CLI = os.getenv("ASTERISK_CLI", "/usr/sbin/asterisk")
_controller_cleanup_lock = asyncio.Lock()


def _parse_contacts(output: str) -> Dict[str, str]:
    contacts: Dict[str, str] = {}
    for line in output.splitlines():
        match = re.search(r"Contact:\s+(\d+)[^\n]*?\b(Avail|Unavail|NonQual|Unknown)\b", line, re.I)
        if match:
            contacts[match.group(1)] = match.group(2).upper()
    return contacts


def _parse_active_channel_details(output: str) -> List[Dict[str, str]]:
    result: List[Dict[str, str]] = []
    for line in output.splitlines():
        fields = line.strip().split("!")
        if len(fields) < 8:
            continue
        channel = fields[0].strip()
        match = re.match(r"PJSIP/(\d+)-", channel, re.I)
        if not match:
            continue
        result.append({
            "channel": channel,
            "extension": match.group(1),
            "context": fields[1].strip(),
            "dialed_extension": fields[2].strip(),
            "state": fields[4].strip().upper(),
            "application": fields[5].strip().upper(),
            "data": fields[6].strip(),
            "caller_id": fields[7].strip(),
        })
    return result


def _parse_active_channels(output: str) -> Dict[str, str]:
    channels: Dict[str, str] = {}
    for channel in _parse_active_channel_details(output):
        extension = channel["extension"]
        state_upper = channel["state"]
        app_upper = channel["application"]
        if app_upper == "CONFBRIDGE":
            channels[extension] = "IN CONFERENCE"
        elif state_upper in {"RING", "RINGING"}:
            channels[extension] = "RINGING"
        elif state_upper == "BUSY" or "BUSY" in app_upper:
            channels[extension] = "BUSY"
        elif state_upper == "UP":
            channels[extension] = "IN CALL"
        else:
            channels[extension] = state_upper
    return channels


def _channel_sequence(channel: str) -> int:
    match = re.search(r"-([0-9a-f]+)$", channel, re.IGNORECASE)
    return int(match.group(1), 16) if match else -1


def _controller_channels(output: str, extension: str, conference: str) -> List[str]:
    wanted_prefix = f"PJSIP/{extension}-"
    wanted_conference = conference.strip().lower()
    channels: List[str] = []
    for channel in _parse_active_channel_details(output):
        name = channel["channel"]
        if not name.startswith(wanted_prefix):
            continue
        if channel["application"] != "CONFBRIDGE":
            continue
        bridge = channel["data"].split(",", 1)[0].strip().lower()
        if bridge == wanted_conference:
            channels.append(name)
    return channels


async def _enforce_single_controller_channel(channels_output: str, extension: str, conference: str) -> str:
    channels = _controller_channels(channels_output, extension, conference)
    if len(channels) <= 1:
        return channels[0] if channels else ""

    # The largest channel sequence is the newest browser session. Keep it and
    # explicitly hang up every older session. This is independent of the SIP
    # registration state, because a browser refresh can leave the old dialog
    # alive briefly while the new browser session is already registered.
    channels.sort(key=_channel_sequence)
    keep = channels[-1]
    for channel in channels[:-1]:
        result = await _run_cli("channel request hangup", channel)
        if "Requested Hangup" not in result and "requested hangup" not in result.lower():
            # Do not fail the endpoint request; Asterisk may have removed the
            # channel between our concise listing and the hangup command.
            continue
    return keep


async def _cleanup_controller_channels(channels_output: str) -> None:
    # Do not derive controllers from the aggregate active-channel state. Scan
    # the actual PJSIP/9xxx ConfBridge legs so cleanup still runs when several
    # sessions exist for the same extension.
    controller_extensions = sorted({
        channel["extension"]
        for channel in _parse_active_channel_details(channels_output)
        if re.fullmatch(r"9\d{3}", channel["extension"])
    })
    if not controller_extensions:
        return

    async with _controller_cleanup_lock:
        for extension in controller_extensions:
            await _enforce_single_controller_channel(
                channels_output,
                extension,
                f"TCCS-CTRL-{extension}",
            )


def _run_cli_sync(*args: str) -> str:
    import subprocess
    process = subprocess.run(
        [ASTERISK_CLI, "-rx", *args],
        capture_output=True,
        text=True,
        timeout=5,
    )
    return process.stdout if process.returncode == 0 else ""


async def _run_cli(*args: str) -> str:
    return await asyncio.to_thread(_run_cli_sync, *args)


async def active_channel_details() -> List[Dict[str, str]]:
    output = await _run_cli("core show channels concise")
    return _parse_active_channel_details(output)


async def endpoint_status() -> List[Dict[str, Any]]:
    try:
        contacts_output, channels_output = await asyncio.gather(
            _run_cli("pjsip show contacts"),
            _run_cli("core show channels concise"),
        )
        if not contacts_output:
            return []

        # Cleanup must happen from the actual channel list, before building the
        # status response. The endpoint is polled by the controller UI, so this
        # gives us a server-side safety net on every poll and every page reload.
        await _cleanup_controller_channels(channels_output)

        # Re-read after cleanup so the API does not report stale duplicate legs.
        channels_output = await _run_cli("core show channels concise")
        contacts = _parse_contacts(contacts_output)
        active_channels = _parse_active_channels(channels_output)

        result: List[Dict[str, Any]] = []
        for extension, contact_state in sorted(contacts.items()):
            channel_state = active_channels.get(extension)
            if channel_state:
                asterisk_state = channel_state
            else:
                asterisk_state = "Not in use" if contact_state == "AVAIL" else contact_state
            result.append({
                "sip_extension": extension,
                "status": "REGISTERED" if contact_state == "AVAIL" else "UNREGISTERED",
                "asterisk_state": asterisk_state,
            })
        return result
    except (OSError, asyncio.TimeoutError):
        return []
