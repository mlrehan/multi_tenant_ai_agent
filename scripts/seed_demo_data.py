"""Seeds a demo tenant, members, and AI resources on top of the tenant
catalog `bootstrap_tenant_catalog.py` seeds.

Development helper for exercising the admin console against realistic data.
Runs through the migrator (table-owner) connection, the same way a real
deployment's seed script would -- catalog rows are deliberately NOT part of
the Alembic migrations (see CLAUDE.md's Phase 6 notes: they're an ops
concern, not schema). The catalog itself (tenant permissions/roles) is
seeded by `seed_tenant_catalog()` from `bootstrap_tenant_catalog.py`, not
duplicated here -- see that module's docstring for why it's a standalone,
production-safe step and not folded into this demo-only script.

Idempotent: safe to re-run. Usage:
    python scripts/seed_demo_data.py [owner-email]
"""

from __future__ import annotations

import asyncio
import sys
import uuid

# Sibling-module import, not `from scripts.bootstrap_tenant_catalog import ...`:
# both this script and bootstrap_tenant_catalog.py are always invoked directly
# (`python scripts/seed_demo_data.py ...`, see DEPLOYMENT.md), which puts
# `scripts/` itself on sys.path rather than the repo root -- `scripts` has no
# `__init__.py` and isn't installed as a package, so `import scripts.x` would
# fail while `import bootstrap_tenant_catalog` resolves correctly.
from bootstrap_tenant_catalog import seed_tenant_catalog
from sqlalchemy import text

from iam_platform.core.config import Settings
from iam_platform.infrastructure.db.session import build_engine_from_dsn

DEFAULT_OWNER_EMAIL = "admin-console-test@example.com"

PLATFORM_PERMISSIONS = [
    ("platform.tenants.create", "tenants", "create", "high", "Create new tenants"),
    ("platform.tenants.suspend", "tenants", "suspend", "critical", "Suspend an existing tenant"),
    ("platform.support.impersonate", "support", "impersonate", "critical",
     "Start a support impersonation session"),
    ("platform.users.read", "users", "read", "medium", "Browse the platform user directory"),
    ("platform.users.manage", "users", "manage", "critical",
     "Suspend or reactivate any platform account"),
]


async def main(owner_email: str) -> None:
    settings = Settings()  # type: ignore[call-arg]
    engine = build_engine_from_dsn(settings.database.migrator_dsn, settings.database)

    async with engine.begin() as conn:
        await seed_tenant_catalog(conn)

        for code, resource, action, risk, description in PLATFORM_PERMISSIONS:
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
                "VALUES (:id, 'platform_super_admin', 'Platform Super Admin', "
                "'Full platform control', true, 1000) ON CONFLICT (code) DO NOTHING"
            ),
            {"id": str(uuid.uuid4())},
        )
        platform_role_id = (
            await conn.execute(text("SELECT id FROM platform_roles WHERE code = 'platform_super_admin'"))
        ).scalar_one()
        for code, *_ in PLATFORM_PERMISSIONS:
            permission_id = (
                await conn.execute(text("SELECT id FROM platform_permissions WHERE code = :c"), {"c": code})
            ).scalar_one()
            await conn.execute(
                text(
                    "INSERT INTO platform_role_permissions (role_id, permission_id) "
                    "VALUES (:r, :p) ON CONFLICT DO NOTHING"
                ),
                {"r": str(platform_role_id), "p": str(permission_id)},
            )

        owner_id = (
            await conn.execute(text("SELECT id FROM users WHERE email = :e"), {"e": owner_email})
        ).scalar()
        if owner_id is None:
            raise SystemExit(
                f"No user with email {owner_email!r}. Register one through the app first, "
                "or pass a different email as the first argument."
            )

        await conn.execute(
            text(
                "INSERT INTO platform_user_roles (id, user_id, role_id, granted_by_user_id) "
                "VALUES (:id, :u, :r, :u) ON CONFLICT DO NOTHING"
            ),
            {"id": str(uuid.uuid4()), "u": str(owner_id), "r": str(platform_role_id)},
        )

        tenant_id = (
            await conn.execute(text("SELECT id FROM tenants WHERE slug = 'northwind'"))
        ).scalar()
        if tenant_id is None:
            tenant_id = uuid.uuid4()
            await conn.execute(
                text(
                    "INSERT INTO tenants (id, slug, display_name, status, owner_user_id) "
                    "VALUES (:id, 'northwind', 'Northwind Traders', 'active', :u)"
                ),
                {"id": str(tenant_id), "u": str(owner_id)},
            )
        else:
            # Re-seeding is how you get back to a working demo, so an earlier
            # round of clicking "suspend" in the console shouldn't leave the
            # demo tenant permanently unusable.
            reactivated = (
                await conn.execute(
                    text(
                        "UPDATE tenants SET status = 'active', suspended_at = NULL, "
                        "suspended_reason = NULL, updated_at = now() "
                        "WHERE id = :t AND status = 'suspended' RETURNING id"
                    ),
                    {"t": str(tenant_id)},
                )
            ).scalar()
            if reactivated is not None:
                print("Demo tenant 'northwind' was suspended -- reactivated it.")

        # Deliberately OUTSIDE the "tenant is new" branch above. These used to
        # live inside it, which made the script idempotent only for the *same*
        # owner email: re-running it with a different one found the existing
        # tenant, skipped the whole block, and then blew up further down on
        # `owner_membership_id`'s .scalar_one() with a bare
        # "No row was found when one was required". Membership is now ensured
        # for whichever owner this run names, existing tenant or not.
        owner_membership_id = (
            await conn.execute(
                text("SELECT id FROM tenant_memberships WHERE tenant_id = :t AND user_id = :u"),
                {"t": str(tenant_id), "u": str(owner_id)},
            )
        ).scalar()
        if owner_membership_id is None:
            owner_membership_id = uuid.uuid4()
            await conn.execute(
                text(
                    "INSERT INTO tenant_memberships "
                    "(id, tenant_id, user_id, status, is_default, metadata, "
                    "created_at, updated_at, joined_at) "
                    "VALUES (:id, :t, :u, 'active', true, '{}'::jsonb, now(), now(), now())"
                ),
                {"id": str(owner_membership_id), "t": str(tenant_id), "u": str(owner_id)},
            )

        owner_role_id = (
            await conn.execute(
                text("SELECT id FROM tenant_roles WHERE code = 'tenant_owner' AND tenant_id IS NULL")
            )
        ).scalar_one()
        await conn.execute(
            text(
                "INSERT INTO tenant_membership_roles "
                "(id, tenant_id, membership_id, role_id, granted_by_user_id) "
                "VALUES (:id, :t, :m, :r, :u) ON CONFLICT DO NOTHING"
            ),
            {"id": str(uuid.uuid4()), "t": str(tenant_id), "m": str(owner_membership_id),
             "r": str(owner_role_id), "u": str(owner_id)},
        )

        for email, job_title, member_status in [
            ("dana@northwind.example", "Support Lead", "active"),
            ("sam@northwind.example", "Analyst", "suspended"),
        ]:
            member_user_id = (
                await conn.execute(text("SELECT id FROM users WHERE email = :e"), {"e": email})
            ).scalar()
            if member_user_id is None:
                member_user_id = uuid.uuid4()
                await conn.execute(
                    text(
                        "INSERT INTO users (id, email, status, security_stamp) "
                        "VALUES (:id, :e, 'active', :s)"
                    ),
                    {"id": str(member_user_id), "e": email, "s": str(uuid.uuid4())},
                )
            await conn.execute(
                text(
                    "INSERT INTO tenant_memberships "
                    "(id, tenant_id, user_id, status, is_default, metadata, "
                    "created_at, updated_at, joined_at, job_title) "
                    "VALUES (:id, :t, :u, :st, false, '{}'::jsonb, now(), now(), now(), :jt) "
                    "ON CONFLICT DO NOTHING"
                ),
                {"id": str(uuid.uuid4()), "t": str(tenant_id), "u": str(member_user_id),
                 "st": member_status, "jt": job_title},
            )

        # Deliberately TENANT-SCOPED, not a platform default (tenant_id NULL).
        #
        # docs/16-schema-ai-resources.md describes platform-default model
        # configurations as "readable by all tenants", but `ai_assistants`
        # carries a plain composite FK
        # `(tenant_id, model_configuration_id) -> model_configurations(tenant_id, id)`
        # with a NOT NULL `tenant_id`, so a tenant assistant can only ever
        # reference a config belonging to that same tenant -- a platform
        # default is unreachable and inserting one fails with
        # `fk_ai_assistants_model_configuration`. Phase 6 hit the same
        # nullable-tenant problem with `tenant_roles` and solved it with a
        # simple single-column FK; `ai_assistants` was not given the same
        # treatment. Seeding a tenant-scoped row works with the schema as it
        # actually is rather than as documented.
        model_config_id = (
            await conn.execute(
                text("SELECT id FROM model_configurations WHERE tenant_id = :t LIMIT 1"),
                {"t": str(tenant_id)},
            )
        ).scalar()
        if model_config_id is None:
            model_config_id = uuid.uuid4()
            await conn.execute(
                text(
                    "INSERT INTO model_configurations (id, tenant_id, model_name, parameters) "
                    "VALUES (:id, :t, 'claude-opus-5', '{}'::jsonb)"
                ),
                {"id": str(model_config_id), "t": str(tenant_id)},
            )

        has_assistants = (
            await conn.execute(
                text("SELECT 1 FROM ai_assistants WHERE tenant_id = :t"), {"t": str(tenant_id)}
            )
        ).scalar()
        if not has_assistants:
            for name, description, assistant_status, visibility in [
                ("Support Copilot", "Answers customer support questions from the help centre",
                 "published", "tenant"),
                ("Contract Analyst", "Summarises and compares vendor contracts", "draft", "restricted"),
            ]:
                await conn.execute(
                    text(
                        "INSERT INTO ai_assistants "
                        "(id, tenant_id, name, description, visibility, owner_membership_id, "
                        "model_configuration_id, status) "
                        "VALUES (:id, :t, :n, :d, :v, :m, :mc, :s)"
                    ),
                    {"id": str(uuid.uuid4()), "t": str(tenant_id), "n": name, "d": description,
                     "v": visibility, "m": str(owner_membership_id),
                     "mc": str(model_config_id), "s": assistant_status},
                )

            knowledge_base_id = uuid.uuid4()
            await conn.execute(
                text(
                    "INSERT INTO knowledge_bases "
                    "(id, tenant_id, name, description, owner_membership_id, visibility, vector_namespace) "
                    "VALUES (:id, :t, 'Help Centre', 'Public help articles and FAQs', :m, 'tenant', :ns)"
                ),
                {"id": str(knowledge_base_id), "t": str(tenant_id),
                 "m": str(owner_membership_id), "ns": f"{tenant_id}/{knowledge_base_id}"},
            )

        print(f"Seeded. Owner: {owner_email}  Tenant: northwind ({tenant_id})")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OWNER_EMAIL))
