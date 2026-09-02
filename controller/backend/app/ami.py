from __future__ import annotations

import asyncio
import os
import re
import uuid
from typing import Dict, List, Optional

AMI_HOST = os.getenv("AMI_HOST", "127.0.0.1")
AMI_PORT = int(os.getenv("AMI_PORT", "5038"))
AMI_USERNAME = os.getenv("AMI_USERNAME", "tccs-controller")
AMI_SECRET = os.getenv("AMI_SECRET", "tccsngp")
AMI_TIMEOUT = float(os.getenv("AMI_TIMEOUT", "5"))
ASTERISK_CLI = os.getenv("ASTERISK_CLI", "/usr/sbin/asterisk")


async def _read_frame(reader: asyncio.StreamReader, timeout: float = AMI_TIMEOUT) -> bytes:
    try:
        return await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=timeout)
    except asyncio.IncompleteReadError as exc:
        raise RuntimeError("AMI connection closed before complete message") from exc
    except asyncio.LimitOverrunError as exc:
        raise RuntimeError("AMI response exceeded reader limit") from exc
    except asyncio.TimeoutError as exc:
        raise RuntimeError("AMI response timeout") from exc


def _parse_message(raw: bytes) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for line in raw.decode(errors="replace").split("\r\n"):
        if ":" in line:
            key, value = line.split(":", 1)
            result[key.strip()] = value.strip()
    return result


async def _read_greeting(reader: asyncio.StreamReader) -> bytes:
    try:
        line = await asyncio.wait_for(reader.readline(), timeout=AMI_TIMEOUT)
    except asyncio.TimeoutError as exc:
        raise RuntimeError("AMI greeting timeout") from exc
    if not line.startswith(b"Asterisk Call Manager/"):
        raise RuntimeError("Invalid AMI greeting")
    return line


async def _read_action_response(reader: asyncio.StreamReader, action_id: str) -> Dict[str, str]:
    deadline = asyncio.get_running_loop().time() + AMI_TIMEOUT
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise RuntimeError(f"AMI action timeout: {action_id}")
        try:
            raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=remaining)
        except asyncio.IncompleteReadError as exc:
            raise RuntimeError("AMI connection closed while waiting for action response") from exc
        except asyncio.LimitOverrunError as exc:
            raise RuntimeError("AMI response exceeded reader limit") from exc
        except asyncio.TimeoutError as exc:
            raise RuntimeError(f"AMI action timeout: {action_id}") from exc
        message = _parse_message(raw)
        if message.get("ActionID") == action_id:
            return message


async def _send_action(writer: asyncio.StreamWriter, action: str) -> None:
    writer.write((action + "\r\n\r\n").encode())
    await writer.drain()


async def _login(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    await _read_greeting(reader)
    login_id = f"tccs-login-{uuid.uuid4()}"
    await _send_action(writer, "\r\n".join([
        "Action: Login", f"ActionID: {login_id}",
        f"Username: {AMI_USERNAME}", f"Secret: {AMI_SECRET}", "Events: off",
    ]))
    response = await _read_action_response(reader, login_id)
    if response.get("Response") != "Success":
        raise RuntimeError(response.get("Message", "AMI login failed"))


async def _run_action(action: str) -> Dict[str, str]:
    reader, writer = await asyncio.wait_for(asyncio.open_connection(AMI_HOST, AMI_PORT), timeout=AMI_TIMEOUT)
    try:
        await _login(reader, writer)
        action_id = f"tccs-action-{uuid.uuid4()}"
        await _send_action(writer, f"ActionID: {action_id}\r\n{action}")
        response = await _read_action_response(reader, action_id)
        if response.get("Response") != "Success":
            raise RuntimeError(response.get("Message", "AMI action failed"))
        return response
    finally:
        try:
            await _send_action(writer, f"Action: Logoff\r\nActionID: tccs-logoff-{uuid.uuid4()}")
        except Exception:
            pass
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def originate_to_conference(extension: str, conference: str) -> Dict[str, str]:
    reader, writer = await asyncio.wait_for(asyncio.open_connection(AMI_HOST, AMI_PORT), timeout=AMI_TIMEOUT)
    try:
        await _login(reader, writer)
        originate_id = f"tccs-originate-{uuid.uuid4()}"
        await _send_action(writer, "\r\n".join([
            "Action: Originate", f"ActionID: {originate_id}",
            f"Channel: PJSIP/{extension}", "Context: tccs-stations", "Exten: 900",
            "Priority: 1", "Timeout: 30000", "CallerID: TCCS Controller <9999>",
            "Async: true", f"Variable: TCCS_CONFERENCE={conference}",
        ]))
        response = await _read_action_response(reader, originate_id)
        if response.get("Response") != "Success":
            raise RuntimeError(response.get("Message", "AMI originate failed"))
        return response
    finally:
        try:
            await _send_action(writer, f"Action: Logoff\r\nActionID: tccs-logoff-{uuid.uuid4()}")
        except Exception:
            pass
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def _find_station_channel_once(extension: str) -> Optional[str]:
    process = await asyncio.create_subprocess_exec(
        ASTERISK_CLI, "-rx", "core show channels concise",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=AMI_TIMEOUT)
    if process.returncode != 0:
        detail = stderr.decode(errors="replace").strip()
        raise RuntimeError(detail or "Unable to inspect active Asterisk channels")

    wanted_prefix = f"PJSIP/{extension}-"
    for line in stdout.decode(errors="replace").splitlines():
        fields = line.strip().split("!")
        if len(fields) < 7:
            continue
        channel = fields[0].strip()
        if channel.startswith(wanted_prefix):
            return channel
    return None


async def station_channel(extension: str) -> Optional[str]:
    """Find a station's PJSIP channel, including an unanswered ringing call.

    Originate is asynchronous, so the channel can appear a few hundred
    milliseconds after the UI's CALLING state. Retry briefly so END works
    even when the operator presses it immediately after CALLING appears.
    """
    for attempt in range(8):
        channel = await _find_station_channel_once(extension)
        if channel:
            return channel
        if attempt < 7:
            await asyncio.sleep(0.15)
    return None


async def conference_channel(extension: str, conference: str = "SECTION01") -> Optional[str]:
    """Find the live PJSIP station channel currently running ConfBridge."""
    process = await asyncio.create_subprocess_exec(
        ASTERISK_CLI, "-rx", "core show channels concise",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=AMI_TIMEOUT)
    if process.returncode != 0:
        detail = stderr.decode(errors="replace").strip()
        raise RuntimeError(detail or "Unable to inspect active Asterisk channels")

    wanted_prefix = f"PJSIP/{extension}-"
    wanted_conference = conference.strip().lower()

    for line in stdout.decode(errors="replace").splitlines():
        fields = line.strip().split("!")
        if len(fields) < 7:
            continue
        channel = fields[0].strip()
        application = fields[5].strip().lower()
        data = fields[6].strip()
        if not channel.startswith(wanted_prefix):
            continue
        if application != "confbridge":
            continue
        bridge = data.split(",", 1)[0].strip().lower()
        if bridge == wanted_conference:
            return channel
    return None


async def hangup_station_channel(extension: str) -> Dict[str, str]:
    channel = await station_channel(extension)
    if not channel:
        raise RuntimeError(f"Station {extension} has no active call to end")
    return await _run_action("\r\n".join(["Action: Hangup", f"Channel: {channel}"]))


async def hangup_conference_channel(extension: str, conference: str = "SECTION01") -> Dict[str, str]:
    channel = await conference_channel(extension, conference)
    if not channel:
        raise RuntimeError(f"Station {extension} is not in conference {conference}")
    return await _run_action("\r\n".join(["Action: Hangup", f"Channel: {channel}"]))


async def mute_conference_channel(extension: str, conference: str = "SECTION01", mute: bool = True) -> Dict[str, str]:
    channel = await conference_channel(extension, conference)
    if not channel:
        raise RuntimeError(f"Station {extension} is not in conference {conference}")
    action = "ConfbridgeMute" if mute else "ConfbridgeUnmute"
    return await _run_action("\r\n".join([f"Action: {action}", f"Conference: {conference}", f"Channel: {channel}"]))
