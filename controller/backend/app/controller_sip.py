from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_db
from .master import require_admin
from .user_management import require_user

router = APIRouter(prefix="/api/v1/controller-management", tags=["controller-sip"])

async def ensure_controller_sip_tables(db: AsyncSession) -> None:
    await db.execute(text("""
        CREATE TABLE IF NOT EXISTS sip_accounts (
            id BIGSERIAL PRIMARY KEY,
            name VARCHAR(128) NOT NULL,
            extension VARCHAR(64) NOT NULL UNIQUE,
            username VARCHAR(128) NOT NULL UNIQUE,
            password VARCHAR(512) NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))
    await db.execute(text("ALTER TABLE controllers ADD COLUMN IF NOT EXISTS sip_account_id BIGINT REFERENCES sip_accounts(id) ON DELETE SET NULL"))
    await db.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_controllers_sip_account ON controllers(sip_account_id) WHERE sip_account_id IS NOT NULL"))
    await db.commit()

@router.get("/sip-accounts")
async def list_sip_accounts(db: AsyncSession = Depends(get_db), admin: dict = Depends(require_admin)):
    result = await db.execute(text("""
        SELECT a.id,a.name,a.extension,a.username,a.enabled,
               c.id AS controller_id,c.code AS controller_code,c.name AS controller_name
        FROM sip_accounts a
        LEFT JOIN controllers c ON c.sip_account_id=a.id
        ORDER BY a.name,a.id
    """))
    return [dict(r._mapping) for r in result]

@router.post("/sip-accounts")
async def create_sip_account(payload: dict = Body(...), db: AsyncSession = Depends(get_db), admin: dict = Depends(require_admin)):
    name = str(payload.get("name") or "").strip()
    extension = str(payload.get("extension") or "").strip()
    username = str(payload.get("username") or extension).strip()
    password = str(payload.get("password") or "")
    if not name or not extension or not username:
        raise HTTPException(status_code=400, detail="SIP account name, extension and username are required")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="SIP password must be at least 6 characters")
    try:
        result = await db.execute(text("""
            INSERT INTO sip_accounts(name,extension,username,password,enabled)
            VALUES(:name,:extension,:username,:password,:enabled)
            RETURNING id,name,extension,username,enabled
        """), {"name": name[:128], "extension": extension[:64], "username": username[:128], "password": password[:512], "enabled": bool(payload.get("enabled", True))})
        row = result.first()
        await db.commit()
        return dict(row._mapping)
    except Exception as exc:
        await db.rollback()
        if "duplicate" in str(exc).lower() or "unique" in str(exc).lower():
            raise HTTPException(status_code=409, detail="SIP extension or username already exists")
        raise

@router.put("/sip-accounts/{account_id}")
async def update_sip_account(account_id: int, payload: dict = Body(...), db: AsyncSession = Depends(get_db), admin: dict = Depends(require_admin)):
    current = (await db.execute(text("SELECT id,name,extension,username,password,enabled FROM sip_accounts WHERE id=:id"), {"id": account_id})).first()
    if current is None:
        raise HTTPException(status_code=404, detail="SIP account not found")
    name = str(payload.get("name", current.name) or "").strip()
    extension = str(payload.get("extension", current.extension) or "").strip()
    username = str(payload.get("username", current.username) or "").strip()
    password = str(payload.get("password") or current.password)
    if not name or not extension or not username or len(password) < 6:
        raise HTTPException(status_code=400, detail="Valid SIP name, extension, username and password are required")
    try:
        result = await db.execute(text("""
            UPDATE sip_accounts SET name=:name,extension=:extension,username=:username,password=:password,enabled=:enabled
            WHERE id=:id RETURNING id,name,extension,username,enabled
        """), {"id": account_id, "name": name[:128], "extension": extension[:64], "username": username[:128], "password": password[:512], "enabled": bool(payload.get("enabled", current.enabled))})
        row = result.first()
        await db.commit()
        return dict(row._mapping)
    except Exception as exc:
        await db.rollback()
        if "duplicate" in str(exc).lower() or "unique" in str(exc).lower():
            raise HTTPException(status_code=409, detail="SIP extension or username already exists")
        raise

@router.delete("/sip-accounts/{account_id}")
async def delete_sip_account(account_id: int, db: AsyncSession = Depends(get_db), admin: dict = Depends(require_admin)):
    row = (await db.execute(text("SELECT id,name FROM sip_accounts WHERE id=:id"), {"id": account_id})).first()
    if row is None:
        raise HTTPException(status_code=404, detail="SIP account not found")
    await db.execute(text("DELETE FROM sip_accounts WHERE id=:id"), {"id": account_id})
    await db.commit()
    return {"status": "DELETED", "id": row.id, "name": row.name}

@router.put("/controllers/{controller_id}/sip-account")
async def assign_controller_sip(controller_id: int, payload: dict = Body(...), db: AsyncSession = Depends(get_db), admin: dict = Depends(require_admin)):
    controller = (await db.execute(text("SELECT id,code FROM controllers WHERE id=:id"), {"id": controller_id})).first()
    if controller is None:
        raise HTTPException(status_code=404, detail="Controller not found")
    raw = payload.get("sip_account_id")
    account_id: Optional[int] = None
    if raw not in (None, ""):
        try:
            account_id = int(raw)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid SIP account")
        account = (await db.execute(text("SELECT id,enabled FROM sip_accounts WHERE id=:id"), {"id": account_id})).first()
        if account is None or not account.enabled:
            raise HTTPException(status_code=400, detail="SIP account not found or disabled")
        used = (await db.execute(text("SELECT code FROM controllers WHERE sip_account_id=:account_id AND id<>:id"), {"account_id": account_id, "id": controller_id})).first()
        if used is not None:
            raise HTTPException(status_code=409, detail=f"SIP account is already assigned to controller {used.code}")
    await db.execute(text("UPDATE controllers SET sip_account_id=:account_id WHERE id=:id"), {"id": controller_id, "account_id": account_id})
    await db.commit()
    return {"status": "ASSIGNED", "controller_id": controller_id, "sip_account_id": account_id}

@router.get("/controllers/{controller_id}/sip-credentials")
async def controller_sip_credentials(controller_id: int, db: AsyncSession = Depends(get_db), user: dict = Depends(require_user)):
    if user.get("role") == "CONTROLLER" and int(user.get("controller_id") or 0) != controller_id:
        raise HTTPException(status_code=403, detail="Controller access is restricted to the assigned controller")
    result = await db.execute(text("""
        SELECT c.id AS controller_id,c.code,c.name,c.section_id,
               a.id AS sip_account_id,a.extension,a.username,a.password,a.enabled
        FROM controllers c LEFT JOIN sip_accounts a ON a.id=c.sip_account_id
        WHERE c.id=:id AND c.enabled=TRUE
    """), {"id": controller_id})
    row = result.first()
    if row is None:
        raise HTTPException(status_code=404, detail="Controller not found or disabled")
    if row.sip_account_id is None or not row.enabled:
        raise HTTPException(status_code=404, detail="No enabled SIP account is assigned to this controller")
    return dict(row._mapping)
