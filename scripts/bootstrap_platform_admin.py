"""Creates the first platform administrator.

Why this has to be a script and not an API call: every platform-role grant
in this system is gated by the self-escalation guard (`can_assign_role`) --
an actor may only grant permissions they already hold. That's exactly right
for day-to-day use, but it means the *first* platform role in a fresh
database can never be created through the API: nobody holds any platform
permission yet to grant one. This script is the one deliberate bypass,
run directly against the database with the migrator (table-owner)
connection -- the same authority Alembic migrations run with.

It also activates the target account if needed. Registration normally
requires clicking an emailed verification link, but this project's email
sender is `ConsoleEmailSender` (see infrastructure/email/console_sender.py)
-- it only ever logs "email queued", in every environment including
production, because a real provider (SES, SendGrid, ...) was deliberately
deferred. Until one is wired in, there is no working self-serve email
verification path at all, so bootstrapping an admin account has to cover
activation too.

Idempotent: safe to re-run. Usage:
    python scripts/bootstrap_platform_admin.py you@example.com
    python scripts/bootstrap_platform_admin.py you@example.com --role-code support_lead
"""

from __future__ import annotations

import argparse
import asyncio
import uuid

from sqlalchemy import text

from iam_platform.core.config import Settings
from iam_platform.infrastructure.db.session import build_engine_from_dsn

DEFAULT_ROLE_CODE = "platform_super_admin"
DEFAULT_ROLE_NAME = "Platform Super Admin"
DEFAULT_ROLE_RANK = 1000

# The full set a genuine first admin needs: create/suspend tenants, and run
# support impersonation. Deliberately not "every platform.* permission that
# happens to exist" -- an admin role should be an intentional, reviewed set.
DEFAULT_PERMISSIONS = [
    ("platform.tenants.create", "tenants", "create", "high", "Create new tenants"),
    ("platform.tenants.suspend", "tenants", "suspend", "critical", "Suspend an existing tenant"),
    ("platform.support.impersonate", "support", "impersonate", "critical",
     "Start a support impersonation session"),
    ("platform.users.read", "users", "read", "medium",
     "Browse the platform user directory"),
    ("platform.users.manage", "users", "manage", "critical",
     "Suspend or reactivate any platform account"),
]


async def main(email: str, role_code: str) -> None:
    settings = Settings()  # type: ignore[call-arg]
    engine = build_engine_from_dsn(settings.database.migrator_dsn, settings.database)

    async with engine.begin() as conn:
        user_id = (await conn.execute(text("SELECT id FROM users WHERE email = :e"), {"e": email})).scalar()
        if user_id is None:
            raise SystemExit(
                f"No user with email {email!r}. Register the account first "
                "(through the console at /register, or POST /v1/auth/register), then re-run this script."
            )

        status = (
            await conn.execute(text("SELECT status FROM users WHERE id = :u"), {"u": str(user_id)})
        ).scalar()
        if status != "active":
            await conn.execute(
                text("UPDATE users SET status = 'active' WHERE id = :u"), {"u": str(user_id)}
            )
            print(f"Activated {email} (was {status!r}) -- no verification email is actually sent yet.")

        for code, resource, action, risk, description in DEFAULT_PERMISSIONS:
            await conn.execute(
                text(
                    "INSERT INTO platform_permissions "
                    "(id, code, scope, resource, action, risk_level, is_system, description) "
                    "VALUES (:id, :code, 'platform', :resource, :action, :risk, true, :description) "
                    "ON CONFLICT (code) DO NOTHING"
                ),
                {"id": str(uuid.uuid4()), "code": code, "resource": resource,
                 "action": action, "risk": risk, "description": description},
            )

        await conn.execute(
            text(
                "INSERT INTO platform_roles (id, code, name, description, is_system, rank) "
                "VALUES (:id, :code, :name, "
                "'Bootstrapped by scripts/bootstrap_platform_admin.py', true, :rank) "
                "ON CONFLICT (code) DO NOTHING"
            ),
            {"id": str(uuid.uuid4()), "code": role_code, "name": DEFAULT_ROLE_NAME,
             "rank": DEFAULT_ROLE_RANK},
        )
        role_id = (
            await conn.execute(text("SELECT id FROM platform_roles WHERE code = :c"), {"c": role_code})
        ).scalar_one()

        for code, *_ in DEFAULT_PERMISSIONS:
            permission_id = (
                await conn.execute(text("SELECT id FROM platform_permissions WHERE code = :c"), {"c": code})
            ).scalar_one()
            await conn.execute(
                text(
                    "INSERT INTO platform_role_permissions (role_id, permission_id) "
                    "VALUES (:r, :p) ON CONFLICT DO NOTHING"
                ),
                {"r": str(role_id), "p": str(permission_id)},
            )

        already_granted = (
            await conn.execute(
                text(
                    "SELECT 1 FROM platform_user_roles "
                    "WHERE user_id = :u AND role_id = :r AND revoked_at IS NULL"
                ),
                {"u": str(user_id), "r": str(role_id)},
            )
        ).scalar()
        if not already_granted:
            await conn.execute(
                text(
                    "INSERT INTO platform_user_roles (id, user_id, role_id, granted_by_user_id) "
                    "VALUES (:id, :u, :r, :u)"
                ),
                {"id": str(uuid.uuid4()), "u": str(user_id), "r": str(role_id)},
            )
            print(f"Granted {role_code!r} to {email}.")
        else:
            print(f"{email} already holds {role_code!r} -- nothing to do.")

    await engine.dispose()
    print(f"\nDone. Sign in as {email} -- you now have: {', '.join(c for c, *_ in DEFAULT_PERMISSIONS)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("email", help="Email of an already-registered account")
    parser.add_argument(
        "--role-code",
        default=DEFAULT_ROLE_CODE,
        help=f"Platform role code to create/use (default: {DEFAULT_ROLE_CODE})",
    )
    args = parser.parse_args()
    asyncio.run(main(args.email, args.role_code))
