from dataclasses import dataclass


@dataclass(frozen=True)
class Station:
    id: int
    name: str
    location: str
    extension: str
    status: str
    section_id: int


STATIONS = [
    Station(1, "AJNI CABIN", "AJNI", "1001", "ONLINE", 1),
    Station(2, "KAMPTEE", "KAMPTEE", "1002", "ONLINE", 1),
    Station(3, "MARAMJHIRI", "MARAMJHIRI", "1003", "CALLING", 1),
    Station(4, "ITARSI", "ITARSI", "1004", "OFFLINE", 1),
    Station(5, "DHARAKHOH", "DHARAKHOH", "1005", "ONLINE", 1),
    Station(6, "WAY STATION 106", "SECTION 01", "1006", "ONLINE", 1),
    Station(7, "WAY STATION 107", "SECTION 01", "1007", "MUTED", 1),
    Station(8, "WAY STATION 108", "SECTION 01", "1008", "ONLINE", 1),
    Station(9, "WAY STATION 109", "SECTION 01", "1009", "ONLINE", 1),
    Station(10, "WAY STATION 110", "SECTION 01", "1010", "ONLINE", 1),
]
