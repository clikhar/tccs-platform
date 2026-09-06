from datetime import datetime, timezone

from .config import settings


def current_status() -> dict[str, str]:
    return {
        "service": settings.app_name,
        "environment": settings.environment,
        "time_utc": datetime.now(timezone.utc).isoformat(),
    }
