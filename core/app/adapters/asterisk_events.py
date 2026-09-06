from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol
from urllib.parse import urlencode, urlsplit, urlunsplit

import websockets


@dataclass(frozen=True)
class AsteriskEvent:
    """Normalized ARI event envelope delivered to TCCS."""

    event_type: str
    application: str | None
    channel_id: str | None
    channel_name: str | None
    payload: dict


class AsteriskEventHandler(Protocol):
    async def __call__(self, event: AsteriskEvent) -> None: ...


class AsteriskEventStream:
    """Receive ARI/Stasis events over WebSocket with reconnect support."""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        app: str = "tccs-core",
        handler: AsteriskEventHandler | None = None,
        reconnect_delay: float = 2.0,
    ) -> None:
        self._url = self._build_url(base_url, username, password, app)
        self._handler = handler
        self._reconnect_delay = reconnect_delay
        self._stop = asyncio.Event()

    @staticmethod
    def _build_url(base_url: str, username: str, password: str, app: str) -> str:
        parsed = urlsplit(base_url.rstrip("/"))
        scheme = "wss" if parsed.scheme == "https" else "ws"
        path = f"{parsed.path.rstrip('/')}/ari/events"
        query = urlencode({"api_key": f"{username}:{password}", "app": app})
        return urlunsplit((scheme, parsed.netloc, path, query, ""))

    @property
    def url(self) -> str:
        return self._url

    async def stop(self) -> None:
        self._stop.set()

    async def run_forever(self) -> None:
        """Keep the ARI event stream alive until stop() is called."""
        while not self._stop.is_set():
            try:
                async with websockets.connect(
                    self._url,
                    ping_interval=20,
                    ping_timeout=20,
                ) as websocket:
                    while not self._stop.is_set():
                        try:
                            message = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                        except asyncio.TimeoutError:
                            continue
                        await self.handle_message(message)
            except (OSError, websockets.WebSocketException):
                if not self._stop.is_set():
                    await asyncio.sleep(self._reconnect_delay)

    async def handle_message(self, message: str | bytes) -> None:
        """Parse one ARI JSON event and dispatch it to the handler."""
        if isinstance(message, bytes):
            message = message.decode("utf-8")
        payload = json.loads(message)
        event = self.normalize(payload)
        if self._handler is not None:
            await self._handler(event)

    @staticmethod
    def normalize(payload: dict) -> AsteriskEvent:
        channel = payload.get("channel") or {}
        return AsteriskEvent(
            event_type=str(payload.get("type", "Unknown")),
            application=payload.get("application"),
            channel_id=channel.get("id"),
            channel_name=channel.get("name"),
            payload=payload,
        )
