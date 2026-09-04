from __future__ import annotations
import base64, hashlib, hmac, json, os, time, re
from typing import Optional
from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from .db import get_db
from .master import TOKEN_SECRET, _hash_password, _verify_password, require_admin
router=APIRouter(); bearer=HTTPBearer(auto_error=False)
ROLE_CONTROLLER="CONTROLLER"; ROLE_TESTROOM="TESTROOM"; ROLE_ADMIN="ADMINISTRATOR"

def _make_token(user_id:int,username:str,role:str,controller_id:Optional[int])->str:
 payload={"sub":user_id,"username":username,"role":"ADMIN" if role in {ROLE_ADMIN,"ADMIN"} else role,"controller_id":controller_id,"exp":int(time.time())+int(os.getenv("TCCS_ADMIN_TOKEN_TTL","28800"))}; raw=base64.urlsafe_b64encode(json.dumps(payload,separators=(",",":")).encode()).decode().rstrip("="); return raw+"."+hmac.new(TOKEN_SECRET.encode(),raw.encode(),hashlib.sha256).hexdigest()

def decode_user_token(token:str)->dict:
 try:
  raw,sig=token.split(".",1); exp=hmac.new(TOKEN_SECRET.encode(),raw.encode(),hashlib.sha256).hexdigest()
  if not hmac.compare_digest(sig,exp): raise ValueError
  payload=json.loads(base64.urlsafe_b64decode((raw+"="*(-len(raw)%4)).encode()).decode())
  if int(payload.get("exp",0))<int(time.time()) or payload.get("role") not in {ROLE_CONTROLLER,ROLE_TESTROOM,ROLE_ADMIN,"ADMIN"}: raise ValueError
  if payload.get("role")=="ADMIN": payload["role"]=ROLE_ADMIN
  return payload
 except Exception: raise HTTPException(status_code=401,detail="Invalid or expired user session")

async def require_user(credentials:Optional[HTTPAuthorizationCredentials]=Depends(bearer),db:AsyncSession=Depends(get_db))->dict:
 if credentials is None or credentials.scheme.lower()!="bearer": raise HTTPException(status_code=401,detail="Login required")
 p=decode_user_token(credentials.credentials); row=(await db.execute(text("SELECT id,username,role,enabled,controller_id FROM admin_users WHERE id=:id"),{"id":p["sub"]})).first()
 if row is None or not row.enabled: raise HTTPException(status_code=403,detail="User account is disabled or unavailable")
 role=ROLE_ADMIN if row.role=="ADMIN" else str(row.role).upper()
 if role not in {ROLE_CONTROLLER,ROLE_TESTROOM,ROLE_ADMIN}: raise HTTPException(status_code=403,detail="User role is not permitted")
 p.update({"id":row.id,"username":row.username,"role":role,"controller_id":row.controller_id}); return p

def require_role_dependency(*roles:str):
 async def dependency(user:dict=Depends(require_user))->dict:
  if user.get("role") not in set(roles): raise HTTPException(status_code=403,detail="Insufficient privileges")
  return user
 return dependency

async def ensure_sip_schema(db:AsyncSession)->None:
 await db.execute(text("""CREATE TABLE IF NOT EXISTS sip_accounts (id BIGSERIAL PRIMARY KEY,name VARCHAR(128) NOT NULL,extension VARCHAR(64) NOT NULL UNIQUE,username VARCHAR(64) NOT NULL UNIQUE,password VARCHAR(256) NOT NULL,enabled BOOLEAN NOT NULL DEFAULT TRUE,created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"""))
 await db.execute(text("ALTER TABLE controllers ADD COLUMN IF NOT EXISTS sip_account_id BIGINT REFERENCES sip_accounts(id) ON DELETE SET NULL"))
 await db.execute(text("CREATE INDEX IF NOT EXISTS idx_controllers_sip_account ON controllers(sip_account_id)"))
 await db.commit()

def _controller_query()->str:
 return "SELECT c.id,c.code,c.name,c.section_id,c.enabled,c.sip_account_id,s.code AS section_code,s.name AS section_name,sa.name AS sip_account_name,sa.extension AS sip_extension,sa.username AS sip_username,sa.password AS sip_password,sa.enabled AS sip_enabled FROM controllers c LEFT JOIN sections s ON s.id=c.section_id LEFT JOIN sip_accounts sa ON sa.id=c.sip_account_id"

@router.post("/auth/login")
async def user_login(payload:dict=Body(...),db:AsyncSession=Depends(get_db)):
 await ensure_sip_schema(db)
 username=str(payload.get("username") or "").strip(); password=str(payload.get("password") or ""); row=(await db.execute(text("SELECT id,username,password_hash,role,enabled,controller_id FROM admin_users WHERE username=:username"),{"username":username})).first()
 if row is None or not row.enabled or not _verify_password(password,row.password_hash): raise HTTPException(status_code=401,detail="Invalid username or password")
 role=ROLE_ADMIN if row.role=="ADMIN" else str(row.role).upper()
 if role not in {ROLE_CONTROLLER,ROLE_TESTROOM,ROLE_ADMIN}: raise HTTPException(status_code=403,detail="User role is not configured")
 if role==ROLE_CONTROLLER and row.controller_id is None: raise HTTPException(status_code=403,detail="Controller user is not assigned to a controller")
 controller=None
 if row.controller_id:
  controller=(await db.execute(text(_controller_query()+" WHERE c.id=:id"),{"id":row.controller_id})).first()
  if controller is None or not controller.enabled: raise HTTPException(status_code=403,detail="Assigned controller is disabled or unavailable")
 return {"access_token":_make_token(row.id,row.username,role,row.controller_id),"token_type":"bearer","expires_in":int(os.getenv("TCCS_ADMIN_TOKEN_TTL","28800")),"username":row.username,"role":role,"controller":dict(controller._mapping) if controller else None}

@router.get("/auth/me")
async def user_me(user:dict=Depends(require_user),db:AsyncSession=Depends(get_db)):
 await ensure_sip_schema(db)
 controller=None
 if user.get("controller_id"):
  row=(await db.execute(text(_controller_query()+" WHERE c.id=:id"),{"id":user["controller_id"]})).first(); controller=dict(row._mapping) if row else None
 return {"id":user["id"],"username":user["username"],"role":user["role"],"controller_id":user.get("controller_id"),"controller":controller}

@router.get("/available-controllers")
async def available_controllers(user:dict=Depends(require_user),db:AsyncSession=Depends(get_db)):
 await ensure_sip_schema(db)
 q=_controller_query()+" WHERE c.enabled=TRUE"; params={}
 if user["role"]==ROLE_CONTROLLER: q+=" AND c.id=:controller_id"; params["controller_id"]=user.get("controller_id")
 q+=" ORDER BY c.code"; result=await db.execute(text(q),params); return [dict(r._mapping) for r in result]

@router.get("/sip-accounts")
async def list_sip_accounts(db:AsyncSession=Depends(get_db),admin:dict=Depends(require_admin)):
 await ensure_sip_schema(db)
 result=await db.execute(text("SELECT sa.id,sa.name,sa.extension,sa.username,sa.enabled,sa.created_at,c.id AS controller_id,c.code AS controller_code,c.name AS controller_name FROM sip_accounts sa LEFT JOIN controllers c ON c.sip_account_id=sa.id ORDER BY sa.extension"))
 return [dict(r._mapping) for r in result]

@router.post("/sip-accounts")
async def create_sip_account(payload:dict=Body(...),db:AsyncSession=Depends(get_db),admin:dict=Depends(require_admin)):
 await ensure_sip_schema(db)
 name=str(payload.get("name") or "").strip(); extension=str(payload.get("extension") or "").strip(); username=str(payload.get("username") or extension).strip(); password=str(payload.get("password") or "")
 if not name: raise HTTPException(400,"SIP account name is required")
 if not re.fullmatch(r"[A-Za-z0-9_.-]{2,64}",extension): raise HTTPException(400,"Invalid SIP extension")
 if not re.fullmatch(r"[A-Za-z0-9_.@+-]{2,64}",username): raise HTTPException(400,"Invalid SIP username")
 if len(password)<6: raise HTTPException(400,"SIP password must be at least 6 characters")
 try:
  r=await db.execute(text("INSERT INTO sip_accounts(name,extension,username,password,enabled) VALUES(:name,:extension,:username,:password,:enabled) RETURNING id,name,extension,username,enabled"),{"name":name[:128],"extension":extension,"username":username,"password":password,"enabled":bool(payload.get("enabled",True))}); await db.commit(); return dict(r.first()._mapping)
 except Exception as exc:
  await db.rollback()
  if "duplicate" in str(exc).lower() or "unique" in str(exc).lower(): raise HTTPException(409,"SIP extension or username already exists")
  raise

@router.put("/sip-accounts/{account_id}")
async def update_sip_account(account_id:int,payload:dict=Body(...),db:AsyncSession=Depends(get_db),admin:dict=Depends(require_admin)):
 await ensure_sip_schema(db)
 current=(await db.execute(text("SELECT id,name,extension,username,password,enabled FROM sip_accounts WHERE id=:id"),{"id":account_id})).first()
 if current is None: raise HTTPException(404,"SIP account not found")
 name=str(payload.get("name",current.name) or "").strip(); extension=str(payload.get("extension",current.extension) or "").strip(); username=str(payload.get("username",current.username) or "").strip(); password=str(payload.get("password",current.password) or "")
 if not name or not re.fullmatch(r"[A-Za-z0-9_.-]{2,64}",extension) or not re.fullmatch(r"[A-Za-z0-9_.@+-]{2,64}",username): raise HTTPException(400,"Invalid SIP account details")
 if len(password)<6: raise HTTPException(400,"SIP password must be at least 6 characters")
 try:
  r=await db.execute(text("UPDATE sip_accounts SET name=:name,extension=:extension,username=:username,password=:password,enabled=:enabled WHERE id=:id RETURNING id,name,extension,username,enabled"),{"id":account_id,"name":name[:128],"extension":extension,"username":username,"password":password,"enabled":bool(payload.get("enabled",current.enabled))}); await db.commit(); return dict(r.first()._mapping)
 except Exception as exc:
  await db.rollback()
  if "duplicate" in str(exc).lower() or "unique" in str(exc).lower(): raise HTTPException(409,"SIP extension or username already exists")
  raise

@router.delete("/sip-accounts/{account_id}")
async def delete_sip_account(account_id:int,db:AsyncSession=Depends(get_db),admin:dict=Depends(require_admin)):
 await ensure_sip_schema(db)
 row=(await db.execute(text("SELECT id,name FROM sip_accounts WHERE id=:id"),{"id":account_id})).first()
 if row is None: raise HTTPException(404,"SIP account not found")
 used=(await db.execute(text("SELECT id,code FROM controllers WHERE sip_account_id=:id"),{"id":account_id})).first()
 if used: raise HTTPException(409,f"SIP account is assigned to controller {used.code}; unassign it first")
 await db.execute(text("DELETE FROM sip_accounts WHERE id=:id"),{"id":account_id}); await db.commit(); return {"status":"DELETED","id":row.id,"name":row.name}

@router.put("/controllers/{controller_id}/sip-account")
async def assign_sip_account(controller_id:int,payload:dict=Body(...),db:AsyncSession=Depends(get_db),admin:dict=Depends(require_admin)):
 await ensure_sip_schema(db)
 if (await db.execute(text("SELECT id FROM controllers WHERE id=:id"),{"id":controller_id})).first() is None: raise HTTPException(404,"Controller not found")
 account_id=payload.get("sip_account_id")
 if account_id in (None,""):
  account_id=None
 else:
  try: account_id=int(account_id)
  except (TypeError,ValueError): raise HTTPException(400,"Invalid SIP account")
  if (await db.execute(text("SELECT id FROM sip_accounts WHERE id=:id AND enabled=TRUE"),{"id":account_id})).first() is None: raise HTTPException(400,"SIP account not found or disabled")
  other=(await db.execute(text("SELECT id,code FROM controllers WHERE sip_account_id=:id AND id<>:controller_id"),{"id":account_id,"controller_id":controller_id})).first()
  if other: raise HTTPException(409,f"SIP account is already assigned to controller {other.code}")
 await db.execute(text("UPDATE controllers SET sip_account_id=:account_id WHERE id=:controller_id"),{"account_id":account_id,"controller_id":controller_id}); await db.commit()
 row=(await db.execute(text(_controller_query()+" WHERE c.id=:id"),{"id":controller_id})).first()
 return dict(row._mapping)

@router.get("/users")
async def list_users(db:AsyncSession=Depends(get_db),admin:dict=Depends(require_admin)):
 result=await db.execute(text("SELECT u.id,u.username,CASE WHEN u.role='ADMIN' THEN 'ADMINISTRATOR' ELSE u.role END AS role,u.enabled,u.controller_id,c.code AS controller_code,c.name AS controller_name FROM admin_users u LEFT JOIN controllers c ON c.id=u.controller_id ORDER BY u.username")); return [dict(r._mapping) for r in result]

@router.post("/users")
async def create_user(payload:dict=Body(...),db:AsyncSession=Depends(get_db),admin:dict=Depends(require_admin)):
 username=str(payload.get("username") or "").strip(); password=str(payload.get("password") or ""); role=str(payload.get("role") or ROLE_CONTROLLER).strip().upper(); cid=payload.get("controller_id")
 if len(username)<2 or len(username)>64: raise HTTPException(400,"Username must be 2-64 characters")
 if len(password)<6: raise HTTPException(400,"Password must be at least 6 characters")
 if role not in {ROLE_CONTROLLER,ROLE_TESTROOM,ROLE_ADMIN}: raise HTTPException(400,"Invalid user role")
 if role==ROLE_CONTROLLER:
  try: cid=int(cid)
  except (TypeError,ValueError): raise HTTPException(400,"Controller user requires a controller assignment")
  if (await db.execute(text("SELECT id FROM controllers WHERE id=:id AND enabled=TRUE"),{"id":cid})).first() is None: raise HTTPException(400,"Assigned controller not found or disabled")
 else: cid=None
 db_role="ADMIN" if role==ROLE_ADMIN else role
 try:
  r=await db.execute(text("INSERT INTO admin_users(username,password_hash,role,enabled,controller_id) VALUES(:username,:password_hash,:role,:enabled,:controller_id) RETURNING id,username,role,enabled,controller_id"),{"username":username,"password_hash":_hash_password(password),"role":db_role,"enabled":bool(payload.get("enabled",True)),"controller_id":cid}); await db.commit(); return dict(r.first()._mapping)
 except Exception as exc:
  await db.rollback()
  if "duplicate" in str(exc).lower() or "unique" in str(exc).lower(): raise HTTPException(409,"Username already exists")
  raise

@router.put("/users/{user_id}")
async def update_user(user_id:int,payload:dict=Body(...),db:AsyncSession=Depends(get_db),admin:dict=Depends(require_admin)):
 current=(await db.execute(text("SELECT id,username,role,enabled,controller_id FROM admin_users WHERE id=:id"),{"id":user_id})).first()
 if current is None: raise HTTPException(404,"User not found")
 username=str(payload.get("username",current.username) or "").strip(); role=str(payload.get("role",current.role) or "").upper(); enabled=bool(payload.get("enabled",current.enabled)); cid=payload.get("controller_id",current.controller_id)
 if role=="ADMIN": role=ROLE_ADMIN
 if role not in {ROLE_CONTROLLER,ROLE_TESTROOM,ROLE_ADMIN}: raise HTTPException(400,"Invalid user role")
 if role==ROLE_CONTROLLER:
  try: cid=int(cid)
  except (TypeError,ValueError): raise HTTPException(400,"Controller user requires a controller assignment")
  if (await db.execute(text("SELECT id FROM controllers WHERE id=:id AND enabled=TRUE"),{"id":cid})).first() is None: raise HTTPException(400,"Assigned controller not found or disabled")
 else: cid=None
 pw=payload.get("password")
 if pw is not None and len(str(pw))<6: raise HTTPException(400,"Password must be at least 6 characters")
 db_role="ADMIN" if role==ROLE_ADMIN else role
 try:
  if pw: await db.execute(text("UPDATE admin_users SET username=:username,password_hash=:password_hash,role=:role,enabled=:enabled,controller_id=:controller_id WHERE id=:id"),{"id":user_id,"username":username,"password_hash":_hash_password(str(pw)),"role":db_role,"enabled":enabled,"controller_id":cid})
  else: await db.execute(text("UPDATE admin_users SET username=:username,role=:role,enabled=:enabled,controller_id=:controller_id WHERE id=:id"),{"id":user_id,"username":username,"role":db_role,"enabled":enabled,"controller_id":cid})
  await db.commit(); r=(await db.execute(text("SELECT id,username,CASE WHEN role='ADMIN' THEN 'ADMINISTRATOR' ELSE role END AS role,enabled,controller_id FROM admin_users WHERE id=:id"),{"id":user_id})).first(); return dict(r._mapping)
 except Exception as exc:
  await db.rollback()
  if "duplicate" in str(exc).lower() or "unique" in str(exc).lower(): raise HTTPException(409,"Username already exists")
  raise

@router.delete("/users/{user_id}")
async def delete_user(user_id:int,db:AsyncSession=Depends(get_db),admin:dict=Depends(require_admin)):
 if user_id==admin.get("id"): raise HTTPException(409,"You cannot delete your own administrator account")
 row=(await db.execute(text("SELECT id,username FROM admin_users WHERE id=:id"),{"id":user_id})).first()
 if row is None: raise HTTPException(404,"User not found")
 await db.execute(text("DELETE FROM admin_users WHERE id=:id"),{"id":user_id}); await db.commit(); return {"status":"DELETED","id":row.id,"username":row.username}

@router.get("/roles")
async def list_roles(admin:dict=Depends(require_admin)):
 return [{"code":ROLE_CONTROLLER,"name":"Controller","description":"Operating page for the assigned controller only","permissions":["OPERATING_ASSIGNED_CONTROLLER"]},{"code":ROLE_TESTROOM,"name":"Testroom","description":"Operating pages for all controllers and recording playback","permissions":["OPERATING_ALL_CONTROLLERS","RECORDINGS"]},{"code":ROLE_ADMIN,"name":"Administrator","description":"Full system administration and operating access","permissions":["ALL"]}]

@router.get("/permissions")
async def permissions(user:dict=Depends(require_user)):
 return {"role":user["role"],"permissions":["OPERATING_ASSIGNED_CONTROLLER"] if user["role"]==ROLE_CONTROLLER else ["OPERATING_ALL_CONTROLLERS","RECORDINGS"] if user["role"]==ROLE_TESTROOM else ["ALL"]}

async def ensure_user_schema(db:AsyncSession)->None:
 await db.execute(text("ALTER TABLE admin_users ADD COLUMN IF NOT EXISTS controller_id BIGINT REFERENCES controllers(id) ON DELETE SET NULL")); await db.execute(text("UPDATE admin_users SET role='ADMIN' WHERE role='ADMINISTRATOR'")); await db.execute(text("CREATE INDEX IF NOT EXISTS idx_admin_users_controller ON admin_users(controller_id)")); await ensure_sip_schema(db)
