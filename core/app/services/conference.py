from dataclasses import dataclass
from enum import Enum


class ParticipantRole(str, Enum):
    CONTROLLER = "controller"
    PARTICIPANT = "participant"


@dataclass
class ConferenceParticipant:
    extension: str
    role: ParticipantRole
    muted: bool = False
    connected: bool = False


class Conference:
    """In-memory conference policy state; persistence is handled by CallService."""

    def __init__(self, conference_id: str, controller: str) -> None:
        self.conference_id = conference_id
        self.participants: dict[str, ConferenceParticipant] = {
            controller: ConferenceParticipant(controller, ParticipantRole.CONTROLLER, connected=True)
        }

    def add_participant(self, extension: str, muted: bool = True) -> ConferenceParticipant:
        participant = ConferenceParticipant(extension, ParticipantRole.PARTICIPANT, muted=muted)
        self.participants[extension] = participant
        return participant

    def connect(self, extension: str) -> None:
        self._require_participant(extension).connected = True

    def mute(self, extension: str) -> None:
        self._require_participant(extension).muted = True

    def unmute(self, extension: str) -> None:
        self._require_participant(extension).muted = False

    def remove(self, extension: str) -> ConferenceParticipant:
        if extension not in self.participants:
            raise ValueError(f"participant {extension} is not in conference")
        if self.participants[extension].role is ParticipantRole.CONTROLLER:
            raise ValueError("controller cannot be removed from persistent conference")
        return self.participants.pop(extension)

    def _require_participant(self, extension: str) -> ConferenceParticipant:
        try:
            return self.participants[extension]
        except KeyError as exc:
            raise ValueError(f"participant {extension} is not in conference") from exc
