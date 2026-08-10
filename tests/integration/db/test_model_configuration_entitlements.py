"""Database-level proof that model entitlements cannot be bypassed.

Everything here runs as the **migrator** role -- deliberately the most
privileged connection in the system, bypassing RLS and every line of
application code. That is the point: the guarantees below are properties of
the schema, so they must hold for a caller who has skipped the use case, the
permission check and row-level security entirely. A test that went through
the application layer would only prove the application layer agrees with
itself.

Two guarantees:

1. An assistant cannot reference a configuration its tenant has not been
   granted (`fk_ai_assistants_model_configuration`).
2. A grant cannot be revoked while an assistant still depends on it -- the
   same constraint read from the other side.

And one property of the migration: an assistant may use a **platform-owned**
configuration, which is precisely what the previous composite FK made
impossible and what the whole change exists to allow.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.integration


async def _seed(migrator_engine: AsyncEngine) -> dict[str, UUID]:
    """Two tenants, one platform-owned configuration, granted only to A."""
    ids = {
        "user_id": uuid4(),
        "tenant_a": uuid4(),
        "tenant_b": uuid4(),
        "membership_a": uuid4(),
        "membership_b": uuid4(),
        "configuration": uuid4(),
    }

    async with migrator_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO users (id, email, status, security_stamp) "
                "VALUES (:id, :email, 'active', :ss)"
            ),
            {
                "id": str(ids["user_id"]),
                "email": f"mc-{ids['user_id']}@example.com",
                "ss": str(uuid4()),
            },
        )
        for label in ("a", "b"):
            tenant_id = ids[f"tenant_{label}"]
            await conn.execute(
                text(
                    "INSERT INTO tenants (id, slug, display_name, status, owner_user_id) "
                    "VALUES (:id, :slug, :name, 'active', :owner)"
                ),
                {
                    "id": str(tenant_id),
                    "slug": f"mc-{label}-{tenant_id.hex[:8]}",
                    "name": f"Tenant {label.upper()}",
                    "owner": str(ids["user_id"]),
                },
            )
            await conn.execute(
                text(
                    "INSERT INTO tenant_memberships "
                    "(id, tenant_id, user_id, status, metadata, joined_at, is_default) "
                    # is_default false for both: one user belongs to two
                    # tenants here, and `ux_tenant_memberships_one_default_per_user`
                    # is a partial unique index that only one of them could claim.
                    "VALUES (:id, :tid, :uid, 'active', '{}'::jsonb, now(), false)"
                ),
                {
                    "id": str(ids[f"membership_{label}"]),
                    "tid": str(tenant_id),
                    "uid": str(ids["user_id"]),
                },
            )

        # Platform-owned: tenant_id IS NULL. Before entitlements this row was
        # unusable by any assistant at all.
        await conn.execute(
            text(
                "INSERT INTO model_configurations (id, tenant_id, model_name, parameters) "
                "VALUES (:id, NULL, 'claude-opus-5', '{}'::jsonb)"
            ),
            {"id": str(ids["configuration"])},
        )
        await conn.execute(
            text(
                "INSERT INTO tenant_model_configurations "
                "(id, tenant_id, model_configuration_id) VALUES (:id, :tid, :cid)"
            ),
            {
                "id": str(uuid4()),
                "tid": str(ids["tenant_a"]),
                "cid": str(ids["configuration"]),
            },
        )
    return ids


async def _insert_assistant(conn, *, tenant_id: UUID, membership_id: UUID, configuration_id: UUID):
    await conn.execute(
        text(
            "INSERT INTO ai_assistants "
            "(id, tenant_id, name, visibility, owner_membership_id, "
            " model_configuration_id, status) "
            "VALUES (:id, :tid, 'a', 'tenant', :mid, :cid, 'draft')"
        ),
        {
            "id": str(uuid4()),
            "tid": str(tenant_id),
            "mid": str(membership_id),
            "cid": str(configuration_id),
        },
    )


class TestEntitlementIsEnforcedByTheDatabase:
    async def test_a_granted_tenant_can_use_a_platform_owned_configuration(
        self, migrator_engine: AsyncEngine
    ) -> None:
        """The capability the old composite FK denied outright."""
        ids = await _seed(migrator_engine)

        async with migrator_engine.begin() as conn:
            await _insert_assistant(
                conn,
                tenant_id=ids["tenant_a"],
                membership_id=ids["membership_a"],
                configuration_id=ids["configuration"],
            )

        async with migrator_engine.connect() as conn:
            count = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM ai_assistants "
                        "WHERE tenant_id = :tid AND model_configuration_id = :cid"
                    ),
                    {"tid": str(ids["tenant_a"]), "cid": str(ids["configuration"])},
                )
            ).scalar()
        assert count == 1

    async def test_an_ungranted_tenant_cannot_use_it_even_as_the_migrator_role(
        self, migrator_engine: AsyncEngine
    ) -> None:
        """Tenant B knows the configuration id -- it is a platform-owned row,
        and ids are guessable in principle. The constraint is what stops it,
        not secrecy and not RLS."""
        ids = await _seed(migrator_engine)

        with pytest.raises(DBAPIError) as excinfo:
            async with migrator_engine.begin() as conn:
                await _insert_assistant(
                    conn,
                    tenant_id=ids["tenant_b"],
                    membership_id=ids["membership_b"],
                    configuration_id=ids["configuration"],
                )
        assert "fk_ai_assistants_model_configuration" in str(excinfo.value)

    async def test_revocation_is_refused_while_an_assistant_depends_on_it(
        self, migrator_engine: AsyncEngine
    ) -> None:
        """The same constraint from the other side: this is what stops an
        operator stranding a production assistant."""
        ids = await _seed(migrator_engine)
        async with migrator_engine.begin() as conn:
            await _insert_assistant(
                conn,
                tenant_id=ids["tenant_a"],
                membership_id=ids["membership_a"],
                configuration_id=ids["configuration"],
            )

        with pytest.raises(DBAPIError) as excinfo:
            async with migrator_engine.begin() as conn:
                await conn.execute(
                    text(
                        "DELETE FROM tenant_model_configurations "
                        "WHERE tenant_id = :tid AND model_configuration_id = :cid"
                    ),
                    {"tid": str(ids["tenant_a"]), "cid": str(ids["configuration"])},
                )
        assert "fk_ai_assistants_model_configuration" in str(excinfo.value)

    async def test_revocation_succeeds_once_no_assistant_uses_it(
        self, migrator_engine: AsyncEngine
    ) -> None:
        ids = await _seed(migrator_engine)

        async with migrator_engine.begin() as conn:
            await conn.execute(
                text(
                    "DELETE FROM tenant_model_configurations "
                    "WHERE tenant_id = :tid AND model_configuration_id = :cid"
                ),
                {"tid": str(ids["tenant_a"]), "cid": str(ids["configuration"])},
            )

        async with migrator_engine.connect() as conn:
            remaining = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM tenant_model_configurations "
                        "WHERE model_configuration_id = :cid"
                    ),
                    {"cid": str(ids["configuration"])},
                )
            ).scalar()
        assert remaining == 0


class TestEntitlementRowsAreTenantIsolated:
    async def test_a_tenant_cannot_see_another_tenants_grants(
        self, engine: AsyncEngine, migrator_engine: AsyncEngine
    ) -> None:
        """Which models a customer is allowed to use describes the shape of
        their deployment, so it is not readable across the boundary."""
        ids = await _seed(migrator_engine)

        async with engine.connect() as conn, conn.begin():
            await conn.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"),
                {"t": str(ids["tenant_b"])},
            )
            visible = (
                await conn.execute(
                    text("SELECT count(*) FROM tenant_model_configurations")
                )
            ).scalar()
        assert visible == 0

    async def test_the_granted_tenant_sees_its_own_grant(
        self, engine: AsyncEngine, migrator_engine: AsyncEngine
    ) -> None:
        """The negative above would pass just as well if the policy hid
        everything from everyone."""
        ids = await _seed(migrator_engine)

        async with engine.connect() as conn, conn.begin():
            await conn.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"),
                {"t": str(ids["tenant_a"])},
            )
            visible = (
                await conn.execute(
                    text("SELECT count(*) FROM tenant_model_configurations")
                )
            ).scalar()
        assert visible == 1

    async def test_no_tenant_context_reveals_nothing(
        self, engine: AsyncEngine, migrator_engine: AsyncEngine
    ) -> None:
        """Fail closed, the same rule every other tenant-owned table follows."""
        await _seed(migrator_engine)

        async with engine.connect() as conn, conn.begin():
            visible = (
                await conn.execute(
                    text("SELECT count(*) FROM tenant_model_configurations")
                )
            ).scalar()
        assert visible == 0

    async def test_a_tenant_cannot_grant_itself_access(
        self, engine: AsyncEngine, migrator_engine: AsyncEngine
    ) -> None:
        """The escalation this table would otherwise invite: no INSERT policy
        exists, so the tenant role cannot write one however it tries."""
        ids = await _seed(migrator_engine)

        with pytest.raises(DBAPIError):
            async with engine.connect() as conn, conn.begin():
                await conn.execute(
                    text("SELECT set_config('app.tenant_id', :t, true)"),
                    {"t": str(ids["tenant_b"])},
                )
                await conn.execute(
                    text(
                        "INSERT INTO tenant_model_configurations "
                        "(id, tenant_id, model_configuration_id) VALUES (:id, :tid, :cid)"
                    ),
                    {
                        "id": str(uuid4()),
                        "tid": str(ids["tenant_b"]),
                        "cid": str(ids["configuration"]),
                    },
                )
