from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_db
from .master import TOKEN_SECRET, _hash_password, _verify_password, require_admin

router = APIRouter()
bearer = HTTPBearer(auto_error=False)
ROLE_CONTROLLER = "CONTROLLER"
ROLE_TESTROOM = "TESTROOM"
ROLE_ADMIN = "ADMINISTRATOR"


def _make_token(user_id: int, username: str, role: str, controller_id: Optional[int]) -> str:
    payload={"sub":user_id,"username":username,"role":"ADMIN" if role in {ROLE_ADMIN,"ADMIN"} else role,"controller_id":controller_id,"exp":int(time.time())+int(os.getenv("TCCS_ADMIN_TOKEN_TTL","28800"))}
    raw=base64.urlsafe_b64encode(json.dumps(payload,separators=(",",":")).encode()).decode().rstrip("=")
    return f"{raw}."+hmac.new(TOKEN_SECRET.encode(),raw.encode(),hashlib.sha256).hexdigest()


def decode_user_token(token: str)->dict:
    try:
        raw,signature=token.split(".",1); expected=hmac.new(TOKEN_SECRET.encode(),raw.encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature,expected): raise ValueError
        padded=raw+"="*(-len(raw)%4); payload=json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        if int(payload.get("exp",0))<int(time.time()): raise ValueError
        if payload.get("role") not in {ROLE_CONTROLLER,ROLE_TESTROOM,ROLE_ADMIN,"ADMIN"}: raise ValueError
        if payload.get("role")=="ADMIN": payload["role"]=ROLE_ADMIN
        return payload
    except Exception: raise HTTPException(status_code=401,detail="Invalid or expired user session")


async def require_user(credentials:Optional[HTTPAuthorizationCredentials]=Depends(bearer),db:AsyncSession=Depends(get_db))->dict:
    if credentials is None or credentials.scheme.lower()!="bearer": raise HTTPException(status_code=401,detail="Login required")
    payload=decode_user_token(credentials.credentials)
    row=(await db.execute(text("SELECT id,username,role,enabled,controller_id FROM admin_users WHERE id=:id"),{"id":payload["sub"]})).first()
    if row is None or not row.enabled: raise HTTPException(status_code=403,detail="User account is disabled or unavailable")
    role=ROLE_ADMIN if row.role=="ADMIN" else str(row.role).upper()
    if role not in {ROLE_CONTROLLER,ROLE_TESTROOM,ROLE_ADMIN}: raise HTTPException(status_code=403,detail="User role is not permitted")
    payload.update({"id":row.id,"username":row.username,"role":role,"controller_id":row.controller_id}); return payload


def require_role_dependency(*roles:str):
    allowed=set(roles)
    async def dependency(user:dict=Depends(require_user))->dict:
        if user.get("role") not in allowed: raise HTTPException(status_code=403,detail="Insufficient privileges")
        return user
    return dependency


@router.post("/auth/login")
async def user_login(payload:dict=Body(...),db:AsyncSession=Depends(get_db)):
    username=str(payload.get("username") or "").strip(); password=str(payload.get("password") or "")
    row=(await db.execute(text("SELECT id,username,password_hash,role,enabled,controller_id FROM admin_users WHERE username=:username"),{"username":username})).first()
    if row is None or not row.enabled or not _verify_password(password,row.password_hash): raise HTTPException(status_code=401,detail="Invalid username or password")
    role=ROLE_ADMIN if row.role=="ADMIN" else str(row.role).upper()
    if role not in {ROLE_CONTROLLER,ROLE_TESTROOM,ROLE_ADMIN}: raise HTTPException(status_code=403,detail="User role is not configured")
    if role==ROLE_CONTROLLER and row.controller_id is None: raise HTTPException(status_code=403,detail="Controller user is not assigned to a controller")
    controller=None
    if row.controller_id:
        controller=(await db.execute(text("SELECT id,code,name,section_id,enabled FROM controllers WHERE id=:id"),{"id":row.controller_id})).first()
        if controller is None or not controller.enabled: raise HTTPException(status_code=403,detail="Assigned controller is disabled or unavailable")
    return {"access_token":_make_token(row.id,row.username,role,row.controller_id),"token_type":"bearer","expires_in":int(os.getenv("TCCS_ADMIN_TOKEN_TTL","28800")),"username":row.username,"role":role,"controller":dict(controller._mapping) if controller else None}


@router.get("/auth/me")
async def user_me(user:dict=Depends(require_user),db:AsyncSession=Depends(get_db)):
    controller=None
    if user.get("controller_id"):
        row=(await db.execute(text("SELECT id,code,name,section_id,enabled FROM controllers WHERE id=:id"),{"id":user["controller_id"]})).first()
        if row: controller=dict(row._mapping)
    return {"id":user["id"],"username":user["username"],"role":user["role"],"controller_id":user.get("controller_id"),"controller":controller}


@router.get("/users")
async def list_users(db:AsyncSession=Depends(get_db),admin:dict=Depends(require_admin)):
    result=await db.execute(text("SELECT u.id,u.username,CASE WHEN u.role='ADMIN' THEN 'ADMINISTRATOR' ELSE u.role END AS role,u.enabled,u.controller_id,c.code AS controller_code,c.name AS controller_name FROM admin_users u LEFT JOIN controllers c ON c.id=u.controller_id ORDER BY u.username")); return [dict(row._mapping) for row in result]


@router.post("/users")
async def create_user(payload:dict=Body(...),db:AsyncSession=Depends(get_db),admin:dict=Depends(require_admin)):
    username=str(payload.get("username") or "").strip(); password=str(payload.get("password") or ""); role=str(payload.get("role") or ROLE_CONTROLLER).strip().upper(); controller_id=payload.get("controller_id")
    if len(username)<2 or len(username)>64: raise HTTPException(status_code=400,detail="Username must be 2-64 characters")
    if len(password)<6: raise HTTPException(status_code=400,detail="Password must be at least 6 characters")
    if role not in {ROLE_CONTROLLER,ROLE_TESTROOM,ROLE_ADMIN}: raise HTTPException(status_code=400,detail="Invalid user role")
    if role==ROLE_CONTROLLER:
        try: controller_id=int(controller_id)
        except (TypeError,ValueError): raise HTTPException(status_code=400,detail="Controller user requires a controller assignment")
        if (await db.execute(text("SELECT id FROM controllers WHERE id=:id AND enabled=TRUE"),{"id":controller_id})).first() is None: raise HTTPException(status_code=400,detail="Assigned controller not found or disabled")
    else: controller_id=None
    try:
        result=await db.execute(text("INSERT INTO admin_users(username,password_hash,role,enabled,controller_id) VALUES(:username,:password_hash,:role,:enabled,:controller_id) RETURNING id,username,role,enabled,controller_id"),{"username":username,"password_hash":_hash_password(password),"role":role,"enabled":bool(payload.get("enabled",True)),"controller_id":controller_id}); await db.commit(); return dict(result.first()._mapping)
    except Exception as exc:
        await db.rollback()
        if "duplicate" in str(exc).lower() or "unique" in str(exc).lower(): raise HTTPException(status_code=409,detail="Username already exists")
        raise


@router.put("/users/{user_id}")
async def update_user(user_id:int,payload:dict=Body(...),db:AsyncSession=Depends(get_db),admin:dict=Depends(require_admin)):
    current=(await db.execute(text("SELECT id,username,role,enabled,controller_id FROM admin_users WHERE id=:id"),{"id":user_id})).first()
    if current is None: raise HTTPException(status_code=404,detail="User not found")
    username=str(payload.get("username",current.username) or "").strip(); role=str(payload.get("role",current.role) or "").upper(); enabled=bool(payload.get("enabled",current.enabled)); controller_id=payload.get("controller_id",current.controller_id)
    if role=="ADMIN": role=ROLE_ADMIN
    if role not in {ROLE_CONTROLLER,ROLE_TESTROOM,ROLE_ADMIN}: raise HTTPException(status_code=400,detail="Invalid user role")
    if role==ROLE_CONTROLLER:
        try: controller_id=int(controller_id)
        except (TypeError,ValueError): raise HTTPException(status_code=400,detail="Controller user requires a controller assignment")
        if (await db.execute(text("SELECT id FROM controllers WHERE id=:id AND enabled=TRUE"),{"id":controller_id})).first() is None: raise HTTPException(status_code=400,detail="Assigned controller not found or disabled")
    else: controller_id=None
    password=payload.get("password")
    if password is not None and len(str(password))<6: raise HTTPException(status_code=400,detail="Password must be at least 6 characters")
    try:
        if password: await db.execute(text("UPDATE admin_users SET username=:username,password_hash=:password_hash,role=:role,enabled=:enabled,controller_id=:controller_id WHERE id=:id"),{"id":user_id,"username":username,"password_hash":_hash_password(str(password)),"role":role,"enabled":enabled,"controller_id":controller_id})
        else: await db.execute(text("UPDATE admin_users SET username=:username,role=:role,enabled=:enabled,controller_id=:controller_id WHERE id=:id"),{"id":user_id,"username":username,"role":role,"enabled":enabled,"controller_id":controller_id})
        await db.commit(); row=(await db.execute(text("SELECT id,username,role,enabled,controller_id FROM admin_users WHERE id=:id"),{"id":user_id})).first(); return dict(row._mapping)
    except Exception as exc:
        await db.rollback()
        if "duplicate" in str(exc).lower() or "unique" in str(exc).lower(): raise HTTPException(status_code=409,detail="Username already exists")
        raise


@router.delete("/users/{user_id}")
async def delete_user(user_id:int,db:AsyncSession=Depends(get_db),admin:dict=Depends(require_admin)):
    if user_id==admin.get("id"): raise HTTPException(status_code=409,detail="You cannot delete your own administrator account")
    row=(await db.execute(text("SELECT id,username FROM admin_users WHERE id=:id"),{"id":user_id})).first()
    if row is None: raise HTTPException(status_code=404,detail="User not found")
    await db.execute(text("DELETE FROM admin_users WHERE id=:id"),{"id":user_id}); await db.commit(); return {"status":"DELETED","id":row.id,"username":row.username}


@router.get("/roles")
async def list_roles(admin:dict=Depends(require_admin)):
    return [{"code":ROLE_CONTROLLER,"name":"Controller","description":"Operating page for the assigned controller only","permissions":["OPERATING_ASSIGNED_CONTROLLER"]},{"code":ROLE_TESTROOM,"name":"Testroom","description":"Operating pages for all controllers and recording playback","permissions":["OPERATING_ALL_CONTROLLERS","RECORDINGS"]},{"code":ROLE_ADMIN,"name":"Administrator","description":"Full system administration and operating access","permissions":["ALL"]}]


@router.get("/permissions")
async def permissions(user:dict=Depends(require_user)):
    if user["role"]==ROLE_CONTROLLER: return {"role":user["role"],"permissions":["OPERATING_ASSIGNED_CONTROLLER"]}
    if user["role"]==ROLE_TESTROOM: return {"role":user["role"],"permissions":["OPERATING_ALL_CONTROLLERS","RECORDINGS"]}
    return {"role":ROLE_ADMIN,"permissions":["ALL"]}


async def ensure_user_schema(db:AsyncSession)->None:
    await db.execute(text("ALTER TABLE admin_users ADD COLUMN IF NOT EXISTS controller_id BIGINT REFERENCES controllers(id) ON DELETE SET NULL"))
    await db.execute(text("CREATE INDEX IF NOT EXISTS idx_admin_users_controller ON admin_users(controller_id)")); await db.commit()
