from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Controller, Section, Station


async def seed_initial_data(session: AsyncSession) -> None:
    section = Section(code="SEC01", name="SECTION 01", enabled=True)
    session.add(section)
    await session.flush()

    session.add(Controller(code="C01", name="Controller 01", section_id=section.id))

    stations = [
        ("101", "AJNI CABIN", "AJNI", "1001", "ONLINE"),
        ("102", "KAMPTEE", "KAMPTEE", "1002", "ONLINE"),
        ("103", "MARAMJHIRI", "MARAMJHIRI", "1003", "CALLING"),
        ("104", "ITARSI", "ITARSI", "1004", "OFFLINE"),
        ("105", "DHARAKHOH", "DHARAKHOH", "1005", "ONLINE"),
        ("106", "WAY STATION 106", "SECTION 01", "1006", "ONLINE"),
        ("107", "WAY STATION 107", "SECTION 01", "1007", "MUTED"),
        ("108", "WAY STATION 108", "SECTION 01", "1008", "ONLINE"),
        ("109", "WAY STATION 109", "SECTION 01", "1009", "ONLINE"),
        ("110", "WAY STATION 110", "SECTION 01", "1010", "ONLINE"),
    ]
    session.add_all([
        Station(
            station_number=number,
            name=name,
            location=location,
            sip_extension=extension,
            section_id=section.id,
            station_type="WAY_STATION",
            enabled=True,
        )
        for number, name, location, extension, _status in stations
    ])
    await session.commit()
