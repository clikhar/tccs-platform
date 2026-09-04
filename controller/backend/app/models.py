from __future__ import annotations

from typing import Optional

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Section(Base):
    __tablename__ = "sections"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class Controller(Base):
    __tablename__ = "controllers"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    section_id: Mapped[Optional[int]] = mapped_column(ForeignKey("sections.id"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class Station(Base):
    __tablename__ = "stations"

    id: Mapped[int] = mapped_column(primary_key=True)
    station_number: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    location: Mapped[Optional[str]] = mapped_column(String(256))
    section_id: Mapped[int] = mapped_column(ForeignKey("sections.id"), index=True)
    sip_extension: Mapped[str] = mapped_column(String(64), unique=True)
    station_type: Mapped[str] = mapped_column(String(32), default="WAY_STATION")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)


class StationGroup(Base):
    __tablename__ = "station_groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    section_id: Mapped[Optional[int]] = mapped_column(ForeignKey("sections.id"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
