from __future__ import annotations

from .ami import originate_to_conference

TCCS_CONFERENCE = "SECTION01"


async def call_station(extension: str):
    return await originate_to_conference(extension, TCCS_CONFERENCE)
