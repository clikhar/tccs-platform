from __future__ import annotations

import asyncio
import os
import uuid
from typing import Dict

AMI_HOST = os.getenv("AMI_HOST", "127.0.0.1")
AMI_PORT = int(os.getenv("AMI_PORT", "5038"))
AMI_USERNAME = os.getenv("AMI_USERNAME", "tccs-controller")
AMI_SECRET = os.getenv("AMI_SECRET", "tccsngp")
AMI_TIMEOUT = float(os.getenv("AMI_TIMEOUT", "5"))


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


async def conference_channel(extension: str, conference: str = "SECTION01") -> str | None:
    reader, writer = await asyncio.wait_for(asyncio.open_connection(AMI_HOST, AMI_PORT), timeout=AMI_TIMEOUT)
    try:
        await _login(reader, writer)
        action_id = f"tccs-command-{uuid.uuid4()}"
        await _send_action(writer, "\r\n".join([
            "Action: Command", f"ActionID: {action_id}",
            "Command: core show channels verbose",
        ]))
        deadline = asyncio.get_running_loop().time() + AMI_TIMEOUT
        chunks: list[str] = []
        while asyncio.get_running_loop().time() < deadline:
            try:
                raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=0.5)
            except asyncio.TimeoutError:
                break
            message = _parse_message(raw)
            if message.get("ActionID") == action_id:
                chunks.append(message.get("Message", ""))
                if message.get("EventList") == "Complete" or message.get("Message"):
                    break
        text = "\n".join(chunks)
        for line in text.splitlines():
            if f"PJSIP/{extension}-" in line and f"ConfBridge({conference})" in line:
                return line.strip().split()[0]
        return None
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
