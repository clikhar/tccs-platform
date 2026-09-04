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
        salt = base64.urlsafe_b64decode(salt_b64.encode()); expected = base64.urlsafe_b64decode(digest_b64.encode())
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iterations))
        return hmac.compare_digest(actual, expected)
    except Exception: return False

def _token(user_id: int, username: str) -> str:
    payload = {"sub": user_id, "username": username, "role": "ADMIN", "exp": int(time.time()) + TOKEN_TTL}
    raw = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    return f"{raw}.{hmac.new(TOKEN_SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest()}"

def _decode_token(token: str) -> dict:
    try:
        raw, signature = token.split(".", 1); expected = hmac.new(TOKEN_SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected): raise ValueError
        padded = raw + "=" * (-len(raw) % 4); payload = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        if int(payload.get("exp", 0)) < int(time.time()) or payload.get("role") != "ADMIN": raise ValueError
        return payload
    except Exception: raise HTTPException(status_code=401, detail="Invalid or expired administrator session")

async def require_admin(credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer), db: AsyncSession = Depends(get_db)) -> dict:
    if credentials is None or credentials.scheme.lower() != "bearer": raise HTTPException(status_code=401, detail="Administrator login required")
    try: payload = _decode_token(credentials.credentials)
    except HTTPException: raise
    result = await db.execute(text("SELECT id, username, role, enabled FROM admin_users WHERE id=:id"), {"id": payload["sub"]}); row = result.first()
    if row is None or not row.enabled or row.role not in {"ADMIN", "ADMINISTRATOR"}: raise HTTPException(status_code=403, detail="Administrator account is disabled or unavailable")
    return dict(row._mapping)

async def ensure_master_tables(db: AsyncSession) -> None:
    await db.execute(text("""CREATE TABLE IF NOT EXISTS station_types (id BIGSERIAL PRIMARY KEY, code VARCHAR(32) NOT NULL UNIQUE, name VARCHAR(128) NOT NULL, enabled BOOLEAN NOT NULL DEFAULT TRUE, priority INTEGER NOT NULL DEFAULT 100)"""))
    await db.execute(text("""CREATE TABLE IF NOT EXISTS admin_users (id BIGSERIAL PRIMARY KEY, username VARCHAR(64) NOT NULL UNIQUE, password_hash VARCHAR(512) NOT NULL, role VARCHAR(32) NOT NULL DEFAULT 'ADMIN', enabled BOOLEAN NOT NULL DEFAULT TRUE, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"""))
    await db.execute(text("""CREATE TABLE IF NOT EXISTS controllers (id BIGSERIAL PRIMARY KEY, code VARCHAR(32) NOT NULL UNIQUE, name VARCHAR(128) NOT NULL, section_id BIGINT REFERENCES sections(id), enabled BOOLEAN NOT NULL DEFAULT TRUE)"""))
    await db.execute(text("""CREATE TABLE IF NOT EXISTS station_groups (id BIGSERIAL PRIMARY KEY, code VARCHAR(32) NOT NULL UNIQUE, name VARCHAR(128) NOT NULL, section_id BIGINT REFERENCES sections(id), enabled BOOLEAN NOT NULL DEFAULT TRUE)"""))
    await db.execute(text("""CREATE TABLE IF NOT EXISTS station_group_members (station_group_id BIGINT NOT NULL REFERENCES station_groups(id) ON DELETE CASCADE, station_id BIGINT NOT NULL REFERENCES stations(id) ON DELETE CASCADE, PRIMARY KEY(station_group_id, station_id))"""))
    await db.execute(text("CREATE INDEX IF NOT EXISTS idx_station_types_enabled ON station_types(enabled)")); await db.execute(text("CREATE INDEX IF NOT EXISTS idx_admin_users_username ON admin_users(username)")); await db.execute(text("CREATE INDEX IF NOT EXISTS idx_controllers_section ON controllers(section_id)")); await db.execute(text("CREATE INDEX IF NOT EXISTS idx_station_groups_section ON station_groups(section_id)")); await db.execute(text("CREATE INDEX IF NOT EXISTS idx_station_group_members_station ON station_group_members(station_id)"))
    existing = await db.execute(text("SELECT id FROM admin_users WHERE username=:username"), {"username": DEFAULT_ADMIN_USER})
    if existing.first() is None: await db.execute(text("INSERT INTO admin_users (username,password_hash,role,enabled) VALUES (:username,:password_hash,'ADMIN',TRUE)"), {"username": DEFAULT_ADMIN_USER,"password_hash":_hash_password(DEFAULT_ADMIN_PASSWORD)})
    await db.execute(text("""INSERT INTO station_types (code,name,priority) VALUES ('WAY_STATION','Way Station',10),('CABIN','Cabin',20),('CONTROL_POINT','Control Point',30),('OTHER','Other',100) ON CONFLICT (code) DO NOTHING""")); await db.commit()

@router.post("/login")
async def admin_login(payload: dict = Body(...), db: AsyncSession = Depends(get_db)):
    username=str(payload.get("username") or "").strip(); password=str(payload.get("password") or ""); row=(await db.execute(text("SELECT id,username,password_hash,role,enabled FROM admin_users WHERE username=:username"),{"username":username})).first()
    if row is None or not row.enabled or row.role not in {"ADMIN","ADMINISTRATOR"} or not _verify_password(password,row.password_hash): raise HTTPException(status_code=401,detail="Invalid administrator username or password")
    return {"access_token":_token(row.id,row.username),"token_type":"bearer","expires_in":TOKEN_TTL,"username":row.username,"role":"ADMINISTRATOR"}

@router.get("/me")
async def admin_me(admin:dict=Depends(require_admin)): return admin

@router.get("/sections")
async def master_sections(db:AsyncSession=Depends(get_db),admin:dict=Depends(require_admin)):
    result=await db.execute(text("SELECT id,code,name,enabled FROM sections ORDER BY id")); return [dict(row._mapping) for row in result]

@router.get("/controllers")
async def master_controllers(db:AsyncSession=Depends(get_db),admin:dict=Depends(require_admin)):
    result=await db.execute(text("SELECT id,code,name,section_id,enabled FROM controllers ORDER BY id")); return [dict(row._mapping) for row in result]
