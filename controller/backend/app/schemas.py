from pydantic import BaseModel, ConfigDict


class SectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    enabled: bool


class StationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    station_number: str
    name: str
    location: str | None
    section_id: int
    sip_extension: str
    station_type: str
    enabled: bool
    priority: int
