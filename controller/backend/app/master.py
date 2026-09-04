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
from sqlalchemy.exc import IntegrityError
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
        if algorithm != "pbkdf2_sha256": return False
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
        if not hmac.compare_digest(signature, expected): raise ValueError
        padded = raw + "=" * (-len(raw) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        if int(payload.get("exp", 0)) < int(time.time()) or payload.get("role") != "ADMIN": raise ValueError
        return payload
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired administrator session")

async def require_admin(credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer), db: AsyncSession = Depends(get_db)) -> dict:
    if credentials is None or credentials.scheme.lower() != "bearer": raise HTTPException(status_code=401, detail="Administrator login required")
    payload = _decode_token(credentials.credentials)
    result = await db.execute(text("SELECT id, username, role, enabled FROM admin_users WHERE id=:id"), {"id": payload["sub"]})
    row = result.first()
    if row is None or not row.enabled or row.role != "ADMIN": raise HTTPException(status_code=403, detail="Administrator account is disabled or unavailable")
    return dict(row._mapping)

async def ensure_master_tables(db: AsyncSession) -> None:
    await db.execute(text("""CREATE TABLE IF NOT EXISTS station_types (id BIGSERIAL PRIMARY KEY, code VARCHAR(32) NOT NULL UNIQUE, name VARCHAR(128) NOT NULL, enabled BOOLEAN NOT NULL DEFAULT TRUE, priority INTEGER NOT NULL DEFAULT 100)"""))
    await db.execute(text("""CREATE TABLE IF NOT EXISTS admin_users (id BIGSERIAL PRIMARY KEY, username VARCHAR(64) NOT NULL UNIQUE, password_hash VARCHAR(512) NOT NULL, role VARCHAR(32) NOT NULL DEFAULT 'ADMIN', enabled BOOLEAN NOT NULL DEFAULT TRUE, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"""))
    await db.execute(text("""CREATE TABLE IF NOT EXISTS controllers (id BIGSERIAL PRIMARY KEY, code VARCHAR(32) NOT NULL UNIQUE, name VARCHAR(128) NOT NULL, section_id BIGINT REFERENCES sections(id), enabled BOOLEAN NOT NULL DEFAULT TRUE)"""))
    await db.execute(text("""CREATE TABLE IF NOT EXISTS station_groups (station_group_id BIGSERIAL PRIMARY KEY, code VARCHAR(32) NOT NULL UNIQUE, name VARCHAR(128) NOT NULL, section_id BIGINT REFERENCES sections(id), enabled BOOLEAN NOT NULL DEFAULT TRUE)"""))
    await db.execute(text("""CREATE TABLE IF NOT EXISTS station_group_members (station_group_id BIGINT NOT NULL REFERENCES station_groups(station_group_id) ON DELETE CASCADE, station_id BIGINT NOT NULL REFERENCES stations(id) ON DELETE CASCADE, PRIMARY KEY(station_group_id, station_id))"""))
    await db.execute(text("CREATE INDEX IF NOT EXISTS idx_station_types_enabled ON station_types(enabled)"))
    await db.execute(text("CREATE INDEX IF NOT EXISTS idx_admin_users_username ON admin_users(username)"))
    await db.execute(text("CREATE INDEX IF NOT EXISTS idx_controllers_section ON controllers(section_id)"))
    await db.execute(text("CREATE INDEX IF NOT EXISTS idx_station_groups_section ON station_groups(section_id)"))
    await db.execute(text("CREATE INDEX IF NOT EXISTS idx_station_group_members_station ON station_group_members(station_id)"))
    existing = await db.execute(text("SELECT id FROM admin_users WHERE username=:username"), {"username": DEFAULT_ADMIN_USER})
    if existing.first() is None:
        await db.execute(text("INSERT INTO admin_users (username,password_hash,role,enabled) VALUES (:username,:password_hash,'ADMIN',TRUE)"), {"username": DEFAULT_ADMIN_USER, "password_hash": _hash_password(DEFAULT_ADMIN_PASSWORD)})
    await db.execute(text("""INSERT INTO station_types (code,name,priority) VALUES ('WAY_STATION','Way Station',10),('CABIN','Cabin',20),('CONTROL_POINT','Control Point',30),('OTHER','Other',100) ON CONFLICT (code) DO NOTHING"""))
    await db.commit()

@router.post("/login")
async def admin_login(payload: dict = Body(...), db: AsyncSession = Depends(get_db)):
    username = str(payload.get("username") or "").strip(); password = str(payload.get("password") or "")
    result = await db.execute(text("SELECT id, username, password_hash, role, enabled FROM admin_users WHERE username=:username"), {"username": username}); row = result.first()
    if row is None or not row.enabled or row.role != "ADMIN" or not _verify_password(password, row.password_hash): raise HTTPException(status_code=401, detail="Invalid administrator username or password")
    return {"access_token": _token(row.id, row.username), "token_type": "bearer", "expires_in": TOKEN_TTL, "username": row.username, "role": row.role}

@router.get("/me")
async def admin_me(admin: dict = Depends(require_admin)): return admin

@router.get("/sections")
async def master_sections(db: AsyncSession = Depends(get_db), admin: dict = Depends(require_admin)):
    result = await db.execute(text("SELECT id,code,name,enabled FROM sections ORDER BY id")); return [dict(row._mapping) for row in result]

@router.post("/sections")
async def create_section(payload: dict = Body(...), db: AsyncSession = Depends(get_db), admin: dict = Depends(require_admin)):
    code = str(payload.get("code") or "").strip().upper(); name = str(payload.get("name") or "").strip()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{1,31}", code): raise HTTPException(status_code=400, detail="Section code must be 2-32 characters using A-Z, 0-9, _ or -")
    if not name: raise HTTPException(status_code=400, detail="Section name is required")
    try:
        result = await db.execute(text("INSERT INTO sections(code,name,enabled) VALUES (:code,:name,:enabled) RETURNING id,code,name,enabled"), {"code": code, "name": name[:128], "enabled": bool(payload.get("enabled", True))}); await db.commit(); return dict(result.first()._mapping)
    except Exception as exc:
        await db.rollback()
        if "duplicate" in str(exc).lower() or "unique" in str(exc).lower(): raise HTTPException(status_code=409, detail="Section code already exists")
        raise

@router.put("/sections/{section_id}")
async def update_section(section_id: int, payload: dict = Body(...), db: AsyncSession = Depends(get_db), admin: dict = Depends(require_admin)):
    result = await db.execute(text("SELECT id,code,name,enabled FROM sections WHERE id=:id"), {"id": section_id}); current = result.first()
    if current is None: raise HTTPException(status_code=404, detail="Section not found")
    code = str(payload.get("code", current.code) or "").strip().upper(); name = str(payload.get("name", current.name) or "").strip()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{1,31}", code): raise HTTPException(status_code=400, detail="Invalid section code")
    if not name: raise HTTPException(status_code=400, detail="Section name is required")
    try:
        result = await db.execute(text("UPDATE sections SET code=:code,name=:name,enabled=:enabled WHERE id=:id RETURNING id,code,name,enabled"), {"id": section_id, "code": code, "name": name[:128], "enabled": bool(payload.get("enabled", current.enabled))}); await db.commit(); return dict(result.first()._mapping)
    except Exception as exc:
        await db.rollback()
        if "duplicate" in str(exc).lower() or "unique" in str(exc).lower(): raise HTTPException(status_code=409, detail="Section code already exists")
        raise

@router.delete("/sections/{section_id}")
async def delete_section(section_id: int, db: AsyncSession = Depends(get_db), admin: dict = Depends(require_admin)):
    result = await db.execute(text("SELECT id,code,name FROM sections WHERE id=:id"), {"id": section_id}); section = result.first()
    if section is None: raise HTTPException(status_code=404, detail="Section not found")
    checks = [("stations", "SELECT COUNT(*) FROM stations WHERE section_id=:id", "station(s)"),("controllers", "SELECT COUNT(*) FROM controllers WHERE section_id=:id", "controller(s)"),("station groups", "SELECT COUNT(*) FROM station_groups WHERE section_id=:id", "station group(s)")]
    for label, query, noun in checks:
        try: used = await db.execute(text(query), {"id": section_id}); count = int(used.scalar_one())
        except Exception: await db.rollback(); raise HTTPException(status_code=500, detail="Unable to check section dependencies")
        if count > 0: raise HTTPException(status_code=409, detail=f"Cannot remove section {section.code}: it is assigned to {count} {noun}; reassign or remove those references first")
    try:
        deleted = await db.execute(text("DELETE FROM sections WHERE id=:id RETURNING id,code,name"), {"id": section_id}); row = deleted.first()
        if row is None: await db.rollback(); raise HTTPException(status_code=404, detail="Section not found")
        await db.commit(); return {"status":"DELETED","id":row.id,"code":row.code,"name":row.name}
    except HTTPException: raise
    except IntegrityError:
        await db.rollback(); raise HTTPException(status_code=409, detail=f"Cannot remove section {section.code}: it is still referenced by another record")
    except Exception as exc:
        await db.rollback(); raise HTTPException(status_code=500, detail=f"Unable to remove section {section.code}: {str(exc)}")

@router.get("/controllers")
async def master_controllers(db: AsyncSession = Depends(get_db), admin: dict = Depends(require_admin)):
    result = await db.execute(text("SELECT c.id,c.code,c.name,c.section_id,c.enabled,s.code AS section_code,s.name AS section_name FROM controllers c LEFT JOIN sections s ON s.id=c.section_id ORDER BY c.code")); return [dict(row._mapping) for row in result]

@router.post("/controllers")
async def create_controller(payload: dict = Body(...), db: AsyncSession = Depends(get_db), admin: dict = Depends(require_admin)):
    code = str(payload.get("code") or "").strip().upper(); name = str(payload.get("name") or "").strip(); section_id = payload.get("section_id")
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{1,31}", code): raise HTTPException(status_code=400, detail="Controller code must be 2-32 characters using A-Z, 0-9, _ or -")
    if not name: raise HTTPException(status_code=400, detail="Controller name is required")
    if section_id in (None, ""): section_id = None
    else:
        try: section_id = int(section_id)
        except (TypeError, ValueError): raise HTTPException(status_code=400, detail="Valid section_id is required")
        if (await db.execute(text("SELECT id FROM sections WHERE id=:id"), {"id":section_id})).first() is None: raise HTTPException(status_code=400, detail="Section not found")
    try:
        result = await db.execute(text("INSERT INTO controllers(code,name,section_id,enabled) VALUES(:code,:name,:section_id,:enabled) RETURNING id,code,name,section_id,enabled"), {"code":code,"name":name[:128],"section_id":section_id,"enabled":bool(payload.get("enabled",True))}); await db.commit(); return dict(result.first()._mapping)
    except Exception as exc:
        await db.rollback()
        if "duplicate" in str(exc).lower() or "unique" in str(exc).lower(): raise HTTPException(status_code=409, detail="Controller code already exists")
        raise

@router.put("/controllers/{controller_id}")
async def update_controller(controller_id:int,payload:dict=Body(...),db:AsyncSession=Depends(get_db),admin:dict=Depends(require_admin)):
    current=(await db.execute(text("SELECT id,code,name,section_id,enabled FROM controllers WHERE id=:id"),{"id":controller_id})).first()
    if current is None: raise HTTPException(status_code=404,detail="Controller not found")
    code=str(payload.get("code",current.code) or "").strip().upper(); name=str(payload.get("name",current.name) or "").strip(); section_id=payload.get("section_id",current.section_id)
    if section_id in (None,""): section_id=None
    else:
        try: section_id=int(section_id)
        except (TypeError,ValueError): raise HTTPException(status_code=400,detail="Valid section_id is required")
        if (await db.execute(text("SELECT id FROM sections WHERE id=:id"),{"id":section_id})).first() is None: raise HTTPException(status_code=400,detail="Section not found")
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{1,31}",code) or not name: raise HTTPException(status_code=400,detail="Valid controller code and name are required")
    try:
        result=await db.execute(text("UPDATE controllers SET code=:code,name=:name,section_id=:section_id,enabled=:enabled WHERE id=:id RETURNING id,code,name,section_id,enabled"),{"id":controller_id,"code":code,"name":name[:128],"section_id":section_id,"enabled":bool(payload.get("enabled",current.enabled))}); await db.commit(); return dict(result.first()._mapping)
    except Exception as exc:
        await db.rollback()
        if "duplicate" in str(exc).lower() or "unique" in str(exc).lower(): raise HTTPException(status_code=409,detail="Controller code already exists")
        raise

@router.delete("/controllers/{controller_id}")
async def delete_controller(controller_id:int,db:AsyncSession=Depends(get_db),admin:dict=Depends(require_admin)):
    controller=(await db.execute(text("SELECT id,code,name FROM controllers WHERE id=:id"),{"id":controller_id})).first()
    if controller is None: raise HTTPException(status_code=404,detail="Controller not found")
    try:
        deleted=await db.execute(text("DELETE FROM controllers WHERE id=:id RETURNING id,code,name"),{"id":controller_id}); row=deleted.first()
        if row is None: await db.rollback(); raise HTTPException(status_code=404,detail="Controller not found")
        await db.commit(); return {"status":"DELETED","id":row.id,"code":row.code,"name":row.name}
    except HTTPException: raise
    except IntegrityError:
        await db.rollback(); raise HTTPException(status_code=409,detail=f"Cannot remove controller {controller.code}: it is still referenced by another record")

@router.get("/station-groups")
async def master_station_groups(db:AsyncSession=Depends(get_db),admin:dict=Depends(require_admin)):
    result=await db.execute(text("""SELECT g.id,g.code,g.name,g.section_id,g.enabled,s.code AS section_code,s.name AS section_name,COUNT(m.station_id) AS member_count FROM station_groups g LEFT JOIN sections s ON s.id=g.section_id LEFT JOIN station_group_members m ON m.station_group_id=g.id GROUP BY g.id,g.code,g.name,g.section_id,g.enabled,s.code,s.name ORDER BY g.code""")); return [dict(row._mapping) for row in result]

@router.get("/station-groups/{group_id}/members")
async def station_group_members(group_id:int,db:AsyncSession=Depends(get_db),admin:dict=Depends(require_admin)):
    exists=(await db.execute(text("SELECT id FROM station_groups WHERE id=:id"),{"id":group_id})).first()
    if exists is None: raise HTTPException(status_code=404,detail="Station group not found")
    result=await db.execute(text("""SELECT s.id,s.station_number,s.name,s.location,s.section_id,s.sip_extension,s.station_type,s.enabled FROM station_group_members m JOIN stations s ON s.id=m.station_id WHERE m.station_group_id=:id ORDER BY s.station_number"""),{"id":group_id}); return [dict(row._mapping) for row in result]

@router.post("/station-groups")
async def create_station_group(payload:dict=Body(...),db:AsyncSession=Depends(get_db),admin:dict=Depends(require_admin)):
    code=str(payload.get("code") or "").strip().upper(); name=str(payload.get("name") or "").strip(); section_id=payload.get("section_id")
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{1,31}",code): raise HTTPException(status_code=400,detail="Station group code must be 2-32 characters using A-Z, 0-9, _ or -")
    if not name: raise HTTPException(status_code=400,detail="Station group name is required")
    if section_id in (None,""): section_id=None
    else:
        try: section_id=int(section_id)
        except (TypeError,ValueError): raise HTTPException(status_code=400,detail="Valid section_id is required")
        if (await db.execute(text("SELECT id FROM sections WHERE id=:id"),{"id":section_id})).first() is None: raise HTTPException(status_code=400,detail="Section not found")
    try:
        result=await db.execute(text("INSERT INTO station_groups(code,name,section_id,enabled) VALUES(:code,:name,:section_id,:enabled) RETURNING id,code,name,section_id,enabled"),{"code":code,"name":name[:128],"section_id":section_id,"enabled":bool(payload.get("enabled",True))}); group=dict(result.first()._mapping); await db.commit(); return group
    except Exception as exc:
        await db.rollback()
        if "duplicate" in str(exc).lower() or "unique" in str(exc).lower(): raise HTTPException(status_code=409,detail="Station group code already exists")
        raise

@router.put("/station-groups/{group_id}")
async def update_station_group(group_id:int,payload:dict=Body(...),db:AsyncSession=Depends(get_db),admin:dict=Depends(require_admin)):
    current=(await db.execute(text("SELECT id,code,name,section_id,enabled FROM station_groups WHERE id=:id"),{"id":group_id})).first()
    if current is None: raise HTTPException(status_code=404,detail="Station group not found")
    code=str(payload.get("code",current.code) or "").strip().upper(); name=str(payload.get("name",current.name) or "").strip(); section_id=payload.get("section_id",current.section_id)
    if section_id in (None,""): section_id=None
    else:
        try: section_id=int(section_id)
        except (TypeError,ValueError): raise HTTPException(status_code=400,detail="Valid section_id is required")
        if (await db.execute(text("SELECT id FROM sections WHERE id=:id"),{"id":section_id})).first() is None: raise HTTPException(status_code=400,detail="Section not found")
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{1,31}",code) or not name: raise HTTPException(status_code=400,detail="Valid station group code and name are required")
    try:
        result=await db.execute(text("UPDATE station_groups SET code=:code,name=:name,section_id=:section_id,enabled=:enabled WHERE id=:id RETURNING id,code,name,section_id,enabled"),{"id":group_id,"code":code,"name":name[:128],"section_id":section_id,"enabled":bool(payload.get("enabled",current.enabled))}); await db.commit(); return dict(result.first()._mapping)
    except Exception as exc:
        await db.rollback()
        if "duplicate" in str(exc).lower() or "unique" in str(exc).lower(): raise HTTPException(status_code=409,detail="Station group code already exists")
        raise

@router.put("/station-groups/{group_id}/members")
async def set_station_group_members(group_id:int,payload:dict=Body(...),db:AsyncSession=Depends(get_db),admin:dict=Depends(require_admin)):
    if (await db.execute(text("SELECT id FROM station_groups WHERE id=:id"),{"id":group_id})).first() is None: raise HTTPException(status_code=404,detail="Station group not found")
    raw=payload.get("station_ids",[])
    if not isinstance(raw,list): raise HTTPException(status_code=400,detail="station_ids must be an array")
    try: station_ids=sorted(set(int(x) for x in raw))
    except (TypeError,ValueError): raise HTTPException(status_code=400,detail="station_ids must contain valid station IDs")
    if station_ids:
        result=await db.execute(text("SELECT id FROM stations WHERE id = ANY(:ids)"),{"ids":station_ids}); found={int(r.id) for r in result}; missing=[x for x in station_ids if x not in found]
        if missing: raise HTTPException(status_code=400,detail=f"Station(s) not found: {', '.join(map(str,missing))}")
    await db.execute(text("DELETE FROM station_group_members WHERE station_group_id=:id"),{"id":group_id})
    for station_id in station_ids: await db.execute(text("INSERT INTO station_group_members(station_group_id,station_id) VALUES(:gid,:sid) ON CONFLICT DO NOTHING"),{"gid":group_id,"sid":station_id})
    await db.commit(); return {"status":"UPDATED","group_id":group_id,"station_ids":station_ids,"member_count":len(station_ids)}

@router.delete("/station-groups/{group_id}")
async def delete_station_group(group_id:int,db:AsyncSession=Depends(get_db),admin:dict=Depends(require_admin)):
    group=(await db.execute(text("SELECT id,code,name FROM station_groups WHERE id=:id"),{"id":group_id})).first()
    if group is None: raise HTTPException(status_code=404,detail="Station group not found")
    await db.execute(text("DELETE FROM station_groups WHERE id=:id"),{"id":group_id}); await db.commit(); return {"status":"DELETED","id":group.id,"code":group.code,"name":group.name}

@router.get("/station-types")
async def master_station_types(db: AsyncSession=Depends(get_db),admin:dict=Depends(require_admin)):
    result=await db.execute(text("SELECT id,code,name,enabled,priority FROM station_types ORDER BY priority,id")); return [dict(row._mapping) for row in result]

@router.post("/station-types")
async def create_station_type(payload:dict=Body(...),db:AsyncSession=Depends(get_db),admin:dict=Depends(require_admin)):
    code=str(payload.get("code") or "").strip().upper(); name=str(payload.get("name") or "").strip()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{1,31}",code): raise HTTPException(status_code=400,detail="Station type code must be 2-32 characters using A-Z, 0-9, _ or -")
    if not name: raise HTTPException(status_code=400,detail="Station type name is required")
    result=await db.execute(text("INSERT INTO station_types(code,name,enabled,priority) VALUES(:code,:name,:enabled,:priority) RETURNING id,code,name,enabled,priority"),{"code":code,"name":name[:128],"enabled":bool(payload.get("enabled",True)),"priority":int(payload.get("priority",100))}); await db.commit(); return dict(result.first()._mapping)

@router.get("/stations")
async def master_stations(db:AsyncSession=Depends(get_db),admin:dict=Depends(require_admin)):
    result=await db.execute(text("SELECT id,station_number,name,location,section_id,sip_extension,station_type,enabled,priority FROM stations ORDER BY priority,station_number")); return [dict(row._mapping) for row in result]

@router.get("/summary")
async def master_summary(db:AsyncSession=Depends(get_db),admin:dict=Depends(require_admin)):
    sections=await db.execute(text("SELECT COUNT(*) FROM sections WHERE enabled")); stations=await db.execute(text("SELECT COUNT(*) FROM stations WHERE enabled")); types=await db.execute(text("SELECT COUNT(*) FROM station_types WHERE enabled")); controllers=await db.execute(text("SELECT COUNT(*) FROM controllers WHERE enabled")); groups=await db.execute(text("SELECT COUNT(*) FROM station_groups WHERE enabled")); return {"sections":int(sections.scalar_one()),"stations":int(stations.scalar_one()),"station_types":int(types.scalar_one()),"controllers":int(controllers.scalar_one()),"station_groups":int(groups.scalar_one())}
