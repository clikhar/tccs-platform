from dataclasses import dataclass
from enum import Enum


class CallMode(str, Enum):
    INDIVIDUAL = "individual"
    GROUP = "group"
    GENERAL = "general"


@dataclass(frozen=True)
class CallPlan:
    """Business-level plan for an omnibus TCCS call.

    A plan describes what Asterisk must execute; it does not execute media
    operations itself. This keeps railway call rules independent of Asterisk.
    """

    mode: CallMode
    source: str
    targets: tuple[str, ...]
    conference_id: str | None = None
    controller_persistent: bool = False


class CallEngine:
    """Build validated individual, group and general-call plans."""

    def individual(self, source: str, target: str) -> CallPlan:
        self._validate(source, target)
        return CallPlan(CallMode.INDIVIDUAL, source, (target,))

    def group(self, source: str, group_members: list[str], conference_id: str) -> CallPlan:
        if not group_members:
            raise ValueError("group call requires at least one target")
        for target in group_members:
            self._validate(source, target)
        return CallPlan(
            CallMode.GROUP,
            source,
            tuple(dict.fromkeys(group_members)),
            conference_id=conference_id,
            controller_persistent=True,
        )

    def general(self, source: str, way_stations: list[str], conference_id: str) -> CallPlan:
        if not way_stations:
            raise ValueError("general call requires at least one target")
        for target in way_stations:
            self._validate(source, target)
        return CallPlan(
            CallMode.GENERAL,
            source,
            tuple(dict.fromkeys(way_stations)),
            conference_id=conference_id,
            controller_persistent=True,
        )

    @staticmethod
    def _validate(source: str, target: str) -> None:
        if not source or not target:
            raise ValueError("source and target are required")
        if not source.isdigit() or not target.isdigit():
            raise ValueError("TCCS extensions must contain digits only")
        if not 2 <= len(source) <= 8 or not 2 <= len(target) <= 8:
            raise ValueError("TCCS extensions must contain 2-8 digits")
