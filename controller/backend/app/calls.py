from __future__ import annotations

from .ami import originate_to_conference
from .db import SessionLocal
from sqlalchemy import text

DEFAULT_TCCS_CONFERENCE = "SECTION01"


def controller_conference(controller_extension: str | None) -> str:
    extension = str(controller_extension or "").strip()
    return f"TCCS-CTRL-{extension}" if extension else DEFAULT_TCCS_CONFERENCE


async def conference_for_station(extension: str) -> str:
    """Resolve the controller-specific bridge for a station's section.

    A station belongs to one section. The section is associated with the
    controller, and that controller's SIP extension is the bridge namespace.
    This keeps station-originated calls out of other controllers' conferences.
    """
    async with SessionLocal() as db:
        result = await db.execute(text("""
            SELECT sa.extension
            FROM stations st
            JOIN controllers c ON c.section_id=st.section_id AND c.enabled=TRUE
            JOIN sip_accounts sa ON sa.id=c.sip_account_id AND sa.enabled=TRUE
            WHERE st.sip_extension=:extension
            ORDER BY c.id
            LIMIT 1
        """), {"extension": str(extension).strip()})
        row = result.first()
    return controller_conference(row.extension if row else None)


async def call_station(extension: str, conference: str | None = None):
    target_conference = conference or await conference_for_station(extension)
    return await originate_to_conference(extension, target_conference)
