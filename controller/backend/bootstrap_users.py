"""One-time bootstrap for the initial TCCS role accounts.

Run from controller/backend after the database is available:
    python bootstrap_users.py
"""
from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.db import SessionLocal
from app.master import _hash_password

DEFAULTS = [
    ("admin", "Admin@123", "ADMIN", None),
    ("testroom", "Testroom@123", "TESTROOM", None),
]


async def main() -> None:
    async with SessionLocal() as db:
        # The existing master startup creates the tables. This also makes the
        # script safe to run immediately after a fresh deployment.
        await db.execute(text("ALTER TABLE admin_users ADD COLUMN IF NOT EXISTS controller_id BIGINT REFERENCES controllers(id) ON DELETE SET NULL"))

        section = (await db.execute(text("SELECT id FROM sections WHERE code='SEC-01' ORDER BY id LIMIT 1"))).first()
        if section is None:
            section = (await db.execute(text("SELECT id FROM sections WHERE enabled=TRUE ORDER BY id LIMIT 1"))).first()
        if section is None:
            raise RuntimeError("No enabled section exists. Create a section before bootstrapping users.")

        controller = (await db.execute(text("SELECT id FROM controllers WHERE enabled=TRUE ORDER BY id LIMIT 1"))).first()
        if controller is None:
            result = await db.execute(text("INSERT INTO controllers(code,name,section_id,enabled) VALUES('CTRL-01','Controller 01',:section_id,TRUE) RETURNING id"), {"section_id": section.id})
            controller = result.first()
            print("Created default controller CTRL-01")

        defaults = DEFAULTS + [("controller", "Controller@123", "CONTROLLER", controller.id)]
        for username, password, role, controller_id in defaults:
            row = (await db.execute(text("SELECT id FROM admin_users WHERE username=:username"), {"username": username})).first()
            if row is None:
                await db.execute(text("INSERT INTO admin_users(username,password_hash,role,enabled,controller_id) VALUES(:username,:password_hash,:role,TRUE,:controller_id)"), {"username":username,"password_hash":_hash_password(password),"role":role,"controller_id":controller_id})
                print(f"Created {role}: {username}")
            else:
                await db.execute(text("UPDATE admin_users SET password_hash=:password_hash,role=:role,enabled=TRUE,controller_id=:controller_id WHERE id=:id"), {"id":row.id,"password_hash":_hash_password(password),"role":role,"controller_id":controller_id})
                print(f"Reset {role}: {username}")
        await db.commit()

    print("\nInitial TCCS accounts:")
    print("  Administrator : admin / Admin@123")
    print("  Testroom      : testroom / Testroom@123")
    print("  Controller    : controller / Controller@123 (CTRL-01)")


if __name__ == "__main__":
    asyncio.run(main())
