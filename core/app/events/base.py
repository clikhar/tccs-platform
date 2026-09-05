from datetime import datetime, timezone
from uuid import uuid4

from ..models import CallEvent


def new_event(event_type: str, call_id: str, source: str | None = None, target: str | None = None) -> CallEvent:
    return CallEvent(
        event_id=str(uuid4()),
        call_id=call_id,
        event_type=event_type,
        occurred_at=datetime.now(timezone.utc).isoformat(),
        source=source,
        target=target,
    )
