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


def _parse_active_channels(output: str) -> Dict[str, str]:
    channels: Dict[str, str] = {}
    for line in output.splitlines():
        # core show channels verbose contains Application and Data after the state.
        # Only consider a station to be IN CALL when its own PJSIP channel is active.
        match = re.match(
            r"\s*PJSIP/(\d+)-\S+\s+\S+\s+\S+\s+\d+\s+(\S+)\s+(\S+)\s*(.*)$",
            line,
            re.I,
        )
        if not match:
            continue
        extension, state, application, data = match.groups()
        state_upper = state.upper()
        app_upper = application.upper()
        data_upper = data.upper()

        if app_upper.startswith("CONFBRIDGE") and "SECTION01" in data_upper:
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


async def _run_cli(*args: str) -> str:
    process = await asyncio.create_subprocess_exec(
        ASTERISK_CLI, "-rx", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await process.communicate()
    if process.returncode != 0:
        return ""
    return stdout.decode(errors="replace")


async def endpoint_status() -> List[Dict[str, Any]]:
    try:
        contacts_output, channels_output = await asyncio.gather(
            _run_cli("pjsip show contacts"),
            _run_cli("core show channels verbose"),
        )
        if not contacts_output:
            return []

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
