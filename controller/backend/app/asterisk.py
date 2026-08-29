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


async def endpoint_status() -> List[Dict[str, Any]]:
    try:
        process = await asyncio.create_subprocess_exec(
            ASTERISK_CLI, "-rx", "pjsip show contacts",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            return []
        contacts = _parse_contacts(stdout.decode(errors="replace"))
        return [
            {
                "sip_extension": extension,
                "status": "REGISTERED" if state == "AVAIL" else "UNREGISTERED",
                "asterisk_state": state,
            }
            for extension, state in sorted(contacts.items())
        ]
    except (OSError, asyncio.TimeoutError):
        return []
