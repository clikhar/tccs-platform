from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class EndpointType(str, Enum):
    CONTROLLER = "controller"
    WAY_STATION = "way_station"
    GATEWAY = "gateway"
    TEST_ROOM = "test_room"


class CallState(str, Enum):
    INITIATED = "initiated"
    RINGING = "ringing"
    CONNECTED = "connected"
    CONFERENCE = "conference"
    ON_HOLD = "on_hold"
    ENDED = "ended"
    FAILED = "failed"


class Device(BaseModel):
    id: str
    name: str
    endpoint_type: EndpointType
    section_id: Optional[str] = None
    extension: str = Field(min_length=2, max_length=8)
    sip_username: str
    enabled: bool = True


class CallEvent(BaseModel):
    event_id: str
    call_id: str
    event_type: str
    occurred_at: str
    source: Optional[str] = None
    target: Optional[str] = None
    metadata: dict[str, str] = Field(default_factory=dict)


class CallRequest(BaseModel):
    source: str
    target: str
    section_id: Optional[str] = None
    mode: str = "individual"


class CallStatus(BaseModel):
    call_id: str
    state: CallState
    source: Optional[str] = None
    target: Optional[str] = None
    conference_id: Optional[str] = None
