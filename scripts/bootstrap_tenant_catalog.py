"""Seeds the tenant-scope permission and role catalog every tenant needs.

Why this has to be a separate step from `bootstrap_platform_admin.py`: that
script seeds the *platform*-scope catalog (disjoint tables, by design -- see
CLAUDE.md's "Confirmed architectural decisions"). Nothing seeds the
*tenant*-scope catalog (`tenant_permissions`, `tenant_roles`: Tenant Owner /
Tenant Administrator / Member) on a fresh deployment, because catalog rows
are deliberately not part of the Alembic migrations (an ops/fixture concern,
not schema -- CLAUDE.md's Phase 6 notes). Until this script existed, the
*only* place that catalog was ever created was `seed_demo_data.py`, which
also creates a fake demo tenant with fake members -- so a real deployment
that (reasonably) skips the demo script ends up with an empty tenant
catalog. `CreateTenant` then can't find the "Tenant Owner" role to assign to
a new tenant's owner, and now refuses instead of silently creating a tenant
whose owner has zero permissions in it (see `TenantOwnerRoleNotSeededError`
in `application/platform_authz/manage_tenants.py`).

Run this once per deployment, right after `bootstrap_platform_admin.py` and
before creating the first tenant. `seed_demo_data.py` also calls
`seed_tenant_catalog()` directly rather than duplicating this list, so there
is exactly one definition of what a fresh tenant's roles/permissions are.

Idempotent: safe to re-run (every insert is `ON CONFLICT DO NOTHING`).
Usage:
    python scripts/bootstrap_tenant_catalog.py
"""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from iam_platform.core.config import Settings
from iam_platform.infrastructure.db.session import build_engine_from_dsn

# The canonical list of every `tenant.*` permission actually checked
# somewhere in application code (verified by grepping `src/` for
# `"tenant\."` literals) -- not an aspirational or example set.
TENANT_PERMISSIONS = [
    ("tenant.users.manage", "users", "manage", "medium", "Suspend, reactivate, and revoke memberships"),
    ("tenant.users.invite", "users", "invite", "medium", "Invite new members to the tenant"),
    ("tenant.roles.manage", "roles", "manage", "high", "Create roles and assign them to members"),
    ("tenant.assistants.create", "assistants", "create", "low", "Create new AI assistants"),
    ("tenant.assistants.publish", "assistants", "publish", "medium", "Publish an assistant for use"),
    ("tenant.assistants.manage", "assistants", "manage", "medium", "Modify any assistant in the tenant"),
    ("tenant.assistants.view_all", "assistants", "view_all", "low",
     "See every assistant regardless of visibility"),
    ("tenant.knowledge_bases.create", "knowledge_bases", "create", "low", "Create knowledge bases"),
    ("tenant.knowledge_bases.query", "knowledge_bases", "query", "low", "Run retrieval queries"),
    ("tenant.knowledge_bases.manage", "knowledge_bases", "manage", "medium", "Modify any knowledge base"),
    ("tenant.documents.upload", "documents", "upload", "low", "Register documents in a knowledge base"),
    ("tenant.conversations.create", "conversations", "create", "low", "Start conversations with assistants"),
    ("tenant.conversations.view", "conversations", "view", "high",
     "View other members' conversation metadata"),
    ("tenant.conversations.view_all", "conversations", "view_all", "high",
     "See every team's handoff queue, not only the teams you staff"),
    # `tenant.provider_credentials.manage` was removed here: bring-your-own-key
    # is no longer a tenant capability. The platform owns every provider
    # credential and every token budget. Deleting the permission row itself
    # from a live database (and from any custom role that already held it) is
    # Phase 3's migration, not this script's job -- this list only governs what
    # a *newly* seeded deployment gets.
    ("tenant.resources.read", "resources", "read", "low", "Read tenant resources"),
]

ALL_TENANT_CODES = [row[0] for row in TENANT_PERMISSIONS]

TENANT_ROLES = [
    ("tenant_owner", "Tenant Owner", "Full control of the tenant", 1000, ALL_TENANT_CODES),
    (
        "tenant_admin",
        "Tenant Administrator",
        "Manages people and resources",
        500,
        # Now identical to tenant_owner's set. The two roles differed only by
        # `tenant.provider_credentials.manage`, which no longer exists -- kept
        # as separate roles because rank (500 vs 1000) still governs who may
        # assign whom, and because collapsing them would silently promote every
        # existing tenant_admin.
        ALL_TENANT_CODES,
    ),
    (
        "member",
        "Member",
        "Uses assistants and knowledge bases",
        10,
        ["tenant.resources.read", "tenant.conversations.create", "tenant.knowledge_bases.query"],
    ),
]


async def seed_tenant_catalog(conn: AsyncConnection) -> None:
    """Inserts the tenant permission catalog and the three built-in tenant
    roles (`tenant_id IS NULL` -- global, available to every tenant), plus
    the role-permission mappings. Must run inside a transaction the caller
    owns; this function neither begins nor commits one."""
    for code, resource, action, risk, description in TENANT_PERMISSIONS:
        await conn.execute(
            text(
                "INSERT INTO tenant_permissions "
                "(id, code, resource, action, risk_level, is_system, tenant_customizable, description) "
                "VALUES (:id, :code, :resource, :action, :risk, true, true, :description) "
                "ON CONFLICT (code) DO NOTHING"
            ),
            {"id": str(uuid.uuid4()), "code": code, "resource": resource,
             "action": action, "risk": risk, "description": description},
        )

    for code, name, description, rank, permission_codes in TENANT_ROLES:
        await conn.execute(
            text(
                "INSERT INTO tenant_roles (id, tenant_id, code, name, description, is_system, rank) "
                "VALUES (:id, NULL, :code, :name, :description, true, :rank) ON CONFLICT DO NOTHING"
            ),
            {"id": str(uuid.uuid4()), "code": code, "name": name,
             "description": description, "rank": rank},
        )
        role_id = (
            await conn.execute(
                text("SELECT id FROM tenant_roles WHERE code = :c AND tenant_id IS NULL"), {"c": code}
            )
        ).scalar_one()
        for permission_code in permission_codes:
            permission_id = (
                await conn.execute(
                    text("SELECT id FROM tenant_permissions WHERE code = :c"), {"c": permission_code}
                )
            ).scalar_one()
            await conn.execute(
                text(
                    "INSERT INTO tenant_role_permissions (role_id, permission_id, tenant_id) "
                    "VALUES (:r, :p, NULL) ON CONFLICT DO NOTHING"
                ),
                {"r": str(role_id), "p": str(permission_id)},
            )


async def main() -> None:
    settings = Settings()  # type: ignore[call-arg]
    engine = build_engine_from_dsn(settings.database.migrator_dsn, settings.database)

    async with engine.begin() as conn:
        await seed_tenant_catalog(conn)

    await engine.dispose()
    print(
        "Done. Tenant catalog seeded: "
        f"{', '.join(code for code, *_ in TENANT_ROLES)} roles, "
        f"{len(TENANT_PERMISSIONS)} permissions."
    )


if __name__ == "__main__":
    asyncio.run(main())
