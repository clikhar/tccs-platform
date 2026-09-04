from __future__ import annotations

from .ami import originate_to_conference

DEFAULT_TCCS_CONFERENCE = "SECTION01"


def controller_conference(controller_extension: str | None) -> str:
    extension = str(controller_extension or "").strip()
    return f"TCCS-CTRL-{extension}" if extension else DEFAULT_TCCS_CONFERENCE


async def call_station(extension: str, conference: str | None = None):
    return await originate_to_conference(extension, conference or DEFAULT_TCCS_CONFERENCE)
