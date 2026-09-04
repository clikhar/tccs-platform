from __future__ import annotations

import asyncio
import os
import re
from typing import Any, Dict, List

ASTERISK_CLI = os.getenv("ASTERISK_CLI", "/usr/sbin/asterisk")


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
        result.append({"channel": channel, "extension": match.group(1), "context": fields[1].strip(), "dialed_extension": fields[2].strip(), "state": fields[4].strip().upper(), "application": fields[5].strip().upper(), "data": fields[6].strip(), "caller_id": fields[7].strip()})
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
        if not name.startswith(wanted_prefix) or channel["application"] != "CONFBRIDGE":
            continue
        bridge = channel["data"].split(",", 1)[0].strip().lower()
        if bridge == wanted_conference:
            channels.append(name)
    return channels


async def _enforce_single_controller_channel(channels_output: str, extension: str, conference: str) -> None:
    channels = _controller_channels(channels_output, extension, conference)
    if len(channels) <= 1:
        return
    channels.sort(key=_channel_sequence)
    for channel in channels[:-1]:
        try:
            await _run_cli(f"channel request hangup {channel}")
        except Exception:
            pass


async def _run_cli(*args: str) -> str:
    process = await asyncio.create_subprocess_exec(ASTERISK_CLI, "-rx", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, _ = await process.communicate()
    if process.returncode != 0:
        return ""
    return stdout.decode(errors="replace")


async def active_channel_details() -> List[Dict[str, str]]:
    output = await _run_cli("core show channels concise")
    return _parse_active_channel_details(output)


async def endpoint_status() -> List[Dict[str, Any]]:
    try:
        contacts_output, channels_output = await asyncio.gather(_run_cli("pjsip show contacts"), _run_cli("core show channels concise"))
        if not contacts_output:
            return []

        contacts = _parse_contacts(contacts_output)
        active_channels = _parse_active_channels(channels_output)

        # Keep one live conference leg per registered browser controller.
        # Controller SIP extensions are 9xxx; derive their conference from the
        # channel itself instead of the obsolete SECTION01 namespace.
        for extension, state in list(active_channels.items()):
            if not re.fullmatch(r"9\d{3}", extension):
                continue
            conference = f"TCCS-CTRL-{extension}"
            await _enforce_single_controller_channel(channels_output, extension, conference)

        result: List[Dict[str, Any]] = []
        for extension, contact_state in sorted(contacts.items()):
            channel_state = active_channels.get(extension)
            if channel_state:
                asterisk_state = channel_state
            else:
                asterisk_state = "Not in use" if contact_state == "AVAIL" else contact_state
            result.append({"sip_extension": extension, "status": "REGISTERED" if contact_state == "AVAIL" else "UNREGISTERED", "asterisk_state": asterisk_state})
        return result
    except (OSError, asyncio.TimeoutError):
        return []
