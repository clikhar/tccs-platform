from __future__ import annotations

import asyncio
import os
from typing import Dict, Optional

AMI_HOST = os.getenv("AMI_HOST", "127.0.0.1")
AMI_PORT = int(os.getenv("AMI_PORT", "5038"))
AMI_USERNAME = os.getenv("AMI_USERNAME", "tccs-controller")
AMI_SECRET = os.getenv("AMI_SECRET", "CHANGE-ME-AMI-PASSWORD")


async def _read_until(reader: asyncio.StreamReader, marker: bytes = b"\r\n\r\n") -> bytes:
    data = b""
    while marker not in data:
        chunk = await reader.read(4096)
        if not chunk:
            break
        data += chunk
    return data


def _parse_message(raw: bytes) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for line in raw.decode(errors="replace").split("\r\n"):
        if ":" in line:
            key, value = line.split(":", 1)
            result[key.strip()] = value.strip()
    return result


async def originate_to_conference(extension: str, conference: str) -> Dict[str, str]:
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(AMI_HOST, AMI_PORT), timeout=5
    )
    try:
        await _read_until(reader)
        login = (
            f"Action: Login\r\n"
            f"Username: {AMI_USERNAME}\r\n"
            f"Secret: {AMI_SECRET}\r\n"
            f"Events: off\r\n\r\n"
        ).encode()
        writer.write(login)
        await writer.drain()
        login_response = _parse_message(await _read_until(reader))
        if login_response.get("Response") != "Success":
            raise RuntimeError(login_response.get("Message", "AMI login failed"))

        action = (
            "Action: Originate\r\n"
            f"Channel: PJSIP/{extension}\r\n"
            "Context: tccs-stations\r\n"
            "Exten: 900\r\n"
            "Priority: 1\r\n"
            "Timeout: 30000\r\n"
            "CallerID: TCCS Controller <9999>\r\n"
            "Async: true\r\n"
            f"Variable: TCCS_CONFERENCE={conference}\r\n\r\n"
        ).encode()
        writer.write(action)
        await writer.drain()
        response = _parse_message(await _read_until(reader))
        if response.get("Response") != "Success":
            raise RuntimeError(response.get("Message", "AMI originate failed"))
        return response
    finally:
        try:
            writer.write(b"Action: Logoff\r\n\r\n")
            await writer.drain()
        except Exception:
            pass
        writer.close()
        await writer.wait_closed()
