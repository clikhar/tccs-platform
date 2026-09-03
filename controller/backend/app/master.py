from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .asterisk import active_channel_details
from .db import get_db

router = APIRouter(prefix="/api/v1/master", tags=["master"])
bearer = HTTPBearer(auto_error=False)

TOKEN_TTL = int(os.getenv("TCCS_ADMIN_TOKEN_TTL", "28800"))
TOKEN_SECRET = os.getenv("TCCS_ADMIN_TOKEN_SECRET", "change-this-tccs-secret")
DEFAULT_ADMIN_USER = os.getenv("TCCS_ADMIN_USER", "admin")
DEFAULT_ADMIN_PASSWORD = os.getenv("TCCS_ADMIN_PASSWORD", "admin123")


def _hash_password(password: str, salt: Optional[bytes] = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 210_000)
    return f"pbkdf2_sha256$210000${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(digest).decode()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt_b64, digest_b64 = stored.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_b64.encode())
        expected = base64.urlsafe_b64decode(digest_b64.encode())
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iterations))
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def _token(user_id: int, username: str) -> str:
    payload = {"sub": user_id, "username": username, "role": "ADMIN", "exp": int(time.time()) + TOKEN_TTL}
    raw = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    signature = hmac.new(TOKEN_SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest()
    return f"{raw}.{signature}"


def _decode_token(token: str) -> dict:
    try:
        raw, signature = token.split(".", 1)
        expected = hmac.new(TOKEN_SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        padded = raw + "=" * (-len(raw) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        if int(payload.get("exp", 0)) < int(time.time()) or payload.get("role") != "ADMIN":
            raise ValueError
        return payload
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired administrator session")


async def require_admin(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Administrator login required")
    payload = _decode_token(credentials.credentials)
    result = await db.execute(text("SELECT id, username, role, enabled FROM admin_users WHERE id=:id"), {"id": payload["sub"]})
    row = result.first()
    if row is None or not row.enabled or row.role != "ADMIN":
        raise HTTPException(status_code=403, detail="Administrator account is disabled or unavailable")
    return dict(row._mapping)


async def ensure_master_tables(db: AsyncSession) -> None:
    await db.execute(text("""
        CREATE TABLE IF NOT EXISTS station_types (
            id BIGSERIAL PRIMARY KEY,
            code VARCHAR(32) NOT NULL UNIQUE,
            name VARCHAR(128) NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            priority INTEGER NOT NULL DEFAULT 100
        )
    """))
    await db.execute(text("""
        CREATE TABLE IF NOT EXISTS admin_users (
            id BIGSERIAL PRIMARY KEY,
            username VARCHAR(64) NOT NULL UNIQUE,
            password_hash VARCHAR(512) NOT NULL,
            role VARCHAR(32) NOT NULL DEFAULT 'ADMIN',
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))
    await db.execute(text("CREATE INDEX IF NOT EXISTS idx_station_types_enabled ON station_types(enabled)"))
    await db.execute(text("CREATE INDEX IF NOT EXISTS idx_admin_users_username ON admin_users(username)"))
    existing = await db.execute(text("SELECT id FROM admin_users WHERE username=:username"), {"username": DEFAULT_ADMIN_USER})
    if existing.first() is None:
        await db.execute(text("INSERT INTO admin_users (username,password_hash,role,enabled) VALUES (:username,:password_hash,'ADMIN',TRUE)"), {
            "username": DEFAULT_ADMIN_USER,
            "password_hash": _hash_password(DEFAULT_ADMIN_PASSWORD),
        })
    await db.execute(text("""
        INSERT INTO station_types (code,name,priority) VALUES
            ('WAY_STATION','Way Station',10),
            ('CABIN','Cabin',20),
            ('CONTROL_POINT','Control Point',30),
            ('OTHER','Other',100)
        ON CONFLICT (code) DO NOTHING
    """))
    await db.commit()


@router.post("/login")
async def admin_login(payload: dict = Body(...), db: AsyncSession = Depends(get_db)):
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    result = await db.execute(text("SELECT id, username, password_hash, role, enabled FROM admin_users WHERE username=:username"), {"username": username})
    row = result.first()
    if row is None or not row.enabled or row.role != "ADMIN" or not _verify_password(password, row.password_hash):
        raise HTTPException(status_code=401, detail="Invalid administrator username or password")
    return {"access_token": _token(row.id, row.username), "token_type": "bearer", "expires_in": TOKEN_TTL, "username": row.username, "role": row.role}


@router.get("/me")
async def admin_me(admin: dict = Depends(require_admin)):
    return admin


@router.get("/sections")
async def master_sections(db: AsyncSession = Depends(get_db), admin: dict = Depends(require_admin)):
    result = await db.execute(text("SELECT id,code,name,enabled FROM sections ORDER BY id"))
    return [dict(row._mapping) for row in result]


@router.post("/sections")
async def create_section(payload: dict = Body(...), db: AsyncSession = Depends(get_db), admin: dict = Depends(require_admin)):
    code = str(payload.get("code") or "").strip().upper()
    name = str(payload.get("name") or "").strip()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{1,31}", code):
        raise HTTPException(status_code=400, detail="Section code must be 2-32 characters using A-Z, 0-9, _ or -")
    if not name:
        raise HTTPException(status_code=400, detail="Section name is required")
    try:
        result = await db.execute(text("INSERT INTO sections(code,name,enabled) VALUES (:code,:name,:enabled) RETURNING id,code,name,enabled"), {"code": code, "name": name[:128], "enabled": bool(payload.get("enabled", True))})
        await db.commit()
        return dict(result.first()._mapping)
    except Exception as exc:
        await db.rollback()
        if "duplicate" in str(exc).lower() or "unique" in str(exc).lower():
            raise HTTPException(status_code=409, detail="Section code already exists")
        raise


@router.put("/sections/{section_id}")
async def update_section(section_id: int, payload: dict = Body(...), db: AsyncSession = Depends(get_db), admin: dict = Depends(require_admin)):
    result = await db.execute(text("SELECT id,code,name,enabled FROM sections WHERE id=:id"), {"id": section_id})
    current = result.first()
    if current is None:
        raise HTTPException(status_code=404, detail="Section not found")
    code = str(payload.get("code", current.code) or "").strip().upper()
    name = str(payload.get("name", current.name) or "").strip()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{1,31}", code):
        raise HTTPException(status_code=400, detail="Invalid section code")
    if not name:
        raise HTTPException(status_code=400, detail="Section name is required")
    try:
        result = await db.execute(text("UPDATE sections SET code=:code,name=:name,enabled=:enabled WHERE id=:id RETURNING id,code,name,enabled"), {"id": section_id, "code": code, "name": name[:128], "enabled": bool(payload.get("enabled", current.enabled))})
        await db.commit()
        return dict(result.first()._mapping)
    except Exception as exc:
        await db.rollback()
        if "duplicate" in str(exc).lower() or "unique" in str(exc).lower():
            raise HTTPException(status_code=409, detail="Section code already exists")
        raise


@router.delete("/sections/{section_id}")
async def delete_section(section_id: int, db: AsyncSession = Depends(get_db), admin: dict = Depends(require_admin)):
    used = await db.execute(text("SELECT COUNT(*) FROM stations WHERE section_id=:id"), {"id": section_id})
    if int(used.scalar_one()) > 0:
        raise HTTPException(status_code=409, detail="Section is assigned to one or more stations; reassign the stations before deleting it")
    result = await db.execute(text("DELETE FROM sections WHERE id=:id RETURNING id,code,name"), {"id": section_id})
    row = result.first()
    if row is None:
        raise HTTPException(status_code=404, detail="Section not found")
    await db.commit()
    return {"status": "DELETED", "id": row.id, "code": row.code, "name": row.name}


@router.get("/station-types")
async def master_station_types(db: AsyncSession = Depends(get_db), admin: dict = Depends(require_admin)):
    result = await db.execute(text("SELECT id,code,name,enabled,priority FROM station_types ORDER BY priority,id"))
    return [dict(row._mapping) for row in result]


@router.post("/station-types")
async def create_station_type(payload: dict = Body(...), db: AsyncSession = Depends(get_db), admin: dict = Depends(require_admin)):
    code = str(payload.get("code") or "").strip().upper()
    name = str(payload.get("name") or "").strip()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{1,31}", code):
        raise HTTPException(status_code=400, detail="Station type code must be 2-32 characters using A-Z, 0-9, _ or -")
    if not name:
        raise HTTPException(status_code=400, detail="Station type name is required")
    try:
        result = await db.execute(text("INSERT INTO station_types(code,name,enabled,priority) VALUES (:code,:name,:enabled,:priority) RETURNING id,code,name,enabled,priority"), {"code": code, "name": name[:128], "enabled": bool(payload.get("enabled", True)), "priority": int(payload.get("priority", 100))})
        await db.commit()
        return dict(result.first()._mapping)
    except Exception as exc:
        await db.rollback()
        if "duplicate" in str(exc).lower() or "unique" in str(exc).lower():
            raise HTTPException(status_code=409, detail="Station type code already exists")
        raise


@router.put("/station-types/{type_id}")
async def update_station_type(type_id: int, payload: dict = Body(...), db: AsyncSession = Depends(get_db), admin: dict = Depends(require_admin)):
    result = await db.execute(text("SELECT id,code,name,enabled,priority FROM station_types WHERE id=:id"), {"id": type_id})
    current = result.first()
    if current is None:
        raise HTTPException(status_code=404, detail="Station type not found")
    code = str(payload.get("code", current.code) or "").strip().upper()
    name = str(payload.get("name", current.name) or "").strip()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{1,31}", code) or not name:
        raise HTTPException(status_code=400, detail="Valid station type code and name are required")
    try:
        result = await db.execute(text("UPDATE station_types SET code=:code,name=:name,enabled=:enabled,priority=:priority WHERE id=:id RETURNING id,code,name,enabled,priority"), {"id": type_id, "code": code, "name": name[:128], "enabled": bool(payload.get("enabled", current.enabled)), "priority": int(payload.get("priority", current.priority))})
        await db.commit()
        return dict(result.first()._mapping)
    except Exception as exc:
        await db.rollback()
        if "duplicate" in str(exc).lower() or "unique" in str(exc).lower():
            raise HTTPException(status_code=409, detail="Station type code already exists")
        raise


@router.delete("/station-types/{type_id}")
async def delete_station_type(type_id: int, db: AsyncSession = Depends(get_db), admin: dict = Depends(require_admin)):
    result = await db.execute(text("SELECT id,code FROM station_types WHERE id=:id"), {"id": type_id})
    row = result.first()
    if row is None:
        raise HTTPException(status_code=404, detail="Station type not found")
    used = await db.execute(text("SELECT COUNT(*) FROM stations WHERE station_type=:code"), {"code": row.code})
    if int(used.scalar_one()) > 0:
        raise HTTPException(status_code=409, detail="Station type is assigned to one or more stations; reassign the stations before deleting it")
    deleted = await db.execute(text("DELETE FROM station_types WHERE id=:id RETURNING id,code"), {"id": type_id})
    deleted_row = deleted.first()
    if deleted_row is None:
        raise HTTPException(status_code=404, detail="Station type not found")
    await db.commit()
    return {"status": "DELETED", "id": deleted_row.id, "code": deleted_row.code}


@router.get("/stations")
async def master_stations(db: AsyncSession = Depends(get_db), admin: dict = Depends(require_admin)):
    result = await db.execute(text("""
        SELECT id,station_number,name,location,section_id,sip_extension,station_type,enabled,priority
        FROM stations ORDER BY priority,station_number
    """))
    return [dict(row._mapping) for row in result]


def _station_payload(payload: dict, current=None) -> dict:
    station_number = str(payload.get("station_number", getattr(current, "station_number", "")) or "").strip()
    name = str(payload.get("name", getattr(current, "name", "")) or "").strip()
    location = str(payload.get("location", getattr(current, "location", "")) or "").strip() or None
    sip = str(payload.get("sip_extension", getattr(current, "sip_extension", "")) or "").strip()
    section_id = payload.get("section_id", getattr(current, "section_id", None))
    station_type = str(payload.get("station_type", getattr(current, "station_type", "WAY_STATION")) or "WAY_STATION").strip().upper()
    enabled = bool(payload.get("enabled", getattr(current, "enabled", True)))
    if not station_number:
        raise HTTPException(status_code=400, detail="Station number is required")
    if not name:
        raise HTTPException(status_code=400, detail="Station name is required")
    if not re.fullmatch(r"10\d{2}", sip):
        raise HTTPException(status_code=400, detail="SIP extension must be a 4-digit 10xx extension")
    try:
        section_id = int(section_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Valid section_id is required")
    return {"station_number": station_number[:32], "name": name[:128], "location": location[:256] if location else None, "sip_extension": sip, "section_id": section_id, "station_type": station_type[:32], "enabled": enabled}


@router.post("/stations")
async def create_master_station(payload: dict = Body(...), db: AsyncSession = Depends(get_db), admin: dict = Depends(require_admin)):
    data = _station_payload(payload)
    section = await db.execute(text("SELECT id FROM sections WHERE id=:id"), {"id": data["section_id"]})
    if section.first() is None:
        raise HTTPException(status_code=400, detail="Section not found")
    station_type = await db.execute(text("SELECT code FROM station_types WHERE code=:code AND enabled=TRUE"), {"code": data["station_type"]})
    if station_type.first() is None:
        raise HTTPException(status_code=400, detail="Station type not found or disabled")
    duplicate = await db.execute(text("SELECT id FROM stations WHERE station_number=:station_number OR sip_extension=:sip"), {"station_number": data["station_number"], "sip": data["sip_extension"]})
    if duplicate.first() is not None:
        raise HTTPException(status_code=409, detail="Station number or SIP extension already exists")
    result = await db.execute(text("""
        INSERT INTO stations(station_number,name,location,section_id,sip_extension,station_type,enabled,priority)
        VALUES(:station_number,:name,:location,:section_id,:sip_extension,:station_type,:enabled,100)
        RETURNING id,station_number,name,location,section_id,sip_extension,station_type,enabled,priority
    """), data)
    await db.commit()
    return dict(result.first()._mapping)


@router.put("/stations/{station_id}")
async def update_master_station(station_id: int, payload: dict = Body(...), db: AsyncSession = Depends(get_db), admin: dict = Depends(require_admin)):
    result = await db.execute(text("SELECT id,station_number,name,location,section_id,sip_extension,station_type,enabled,priority FROM stations WHERE id=:id"), {"id": station_id})
    current = result.first()
    if current is None:
        raise HTTPException(status_code=404, detail="Station not found")
    data = _station_payload(payload, current)
    section = await db.execute(text("SELECT id FROM sections WHERE id=:id"), {"id": data["section_id"]})
    if section.first() is None:
        raise HTTPException(status_code=400, detail="Section not found")
    station_type = await db.execute(text("SELECT code FROM station_types WHERE code=:code AND enabled=TRUE"), {"code": data["station_type"]})
    if station_type.first() is None:
        raise HTTPException(status_code=400, detail="Station type not found or disabled")
    duplicate = await db.execute(text("SELECT id FROM stations WHERE (station_number=:station_number OR sip_extension=:sip) AND id<>:id"), {"station_number": data["station_number"], "sip": data["sip_extension"], "id": station_id})
    if duplicate.first() is not None:
        raise HTTPException(status_code=409, detail="Station number or SIP extension already exists")
    result = await db.execute(text("""
        UPDATE stations SET station_number=:station_number,name=:name,location=:location,section_id=:section_id,
            sip_extension=:sip_extension,station_type=:station_type,enabled=:enabled
        WHERE id=:id
        RETURNING id,station_number,name,location,section_id,sip_extension,station_type,enabled,priority
    """), dict(data, id=station_id))
    await db.commit()
    return dict(result.first()._mapping)


@router.delete("/stations/{station_id}")
async def delete_master_station(station_id: int, db: AsyncSession = Depends(get_db), admin: dict = Depends(require_admin)):
    result = await db.execute(text("SELECT id,station_number,sip_extension FROM stations WHERE id=:id"), {"id": station_id})
    station = result.first()
    if station is None:
        raise HTTPException(status_code=404, detail="Station not found")
    channels = await active_channel_details()
    if any(c.get("extension") == station.sip_extension and c.get("channel") for c in channels):
        raise HTTPException(status_code=409, detail="Station has an active call; disconnect it before removing the subscriber")
    await db.execute(text("DELETE FROM stations WHERE id=:id"), {"id": station_id})
    await db.commit()
    return {"status": "DELETED", "station_id": station.id, "station_number": station.station_number}


@router.get("/summary")
async def master_summary(db: AsyncSession = Depends(get_db), admin: dict = Depends(require_admin)):
    sections = await db.execute(text("SELECT COUNT(*) FROM sections WHERE enabled"))
    stations = await db.execute(text("SELECT COUNT(*) FROM stations WHERE enabled"))
    types = await db.execute(text("SELECT COUNT(*) FROM station_types WHERE enabled"))
    return {"sections": int(sections.scalar_one()), "stations": int(stations.scalar_one()), "station_types": int(types.scalar_one())}
