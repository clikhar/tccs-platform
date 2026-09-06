from __future__ import annotations

from .asterisk_events import AsteriskEvent
from ..db import AsyncSessionLocal
from ..services.call_service import CallService


class AsteriskEventProcessor:
    """Bridge ARI events into the persistent TCCS call service."""

    async def __call__(self, event: AsteriskEvent) -> None:
        async with AsyncSessionLocal() as session:
            await CallService(session).handle_asterisk_event(event)
