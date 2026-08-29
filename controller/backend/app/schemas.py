from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class SectionOut(BaseModel):
    id: int
    code: str
    name: str
    enabled: bool

    class Config:
        orm_mode = True


class StationOut(BaseModel):
    id: int
    station_number: str
    name: str
    location: Optional[str]
    section_id: int
    sip_extension: str
    station_type: str
    enabled: bool
    priority: int

    class Config:
        orm_mode = True
