"""The RLS proof suite promised in docs/18-schema-rls-and-migrations.md --
exercises Row-Level Security directly against real Postgres roles, not
through the application layer, so these tests prove the *database* enforces
isolation independently of whether application code remembers to filter.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.integration

NOW = datetime.now(UTC)


async def _seed_user_and_two_tenants(migrator_engine: AsyncEngine):
    user_id, tenant_a, tenant_b = uuid4(), uuid4(), uuid4()
    async with migrator_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO users (id, email, status, security_stamp) "
                "VALUES (:id, :email, 'active', :ss)"
            ),
            {"id": str(user_id), "email": f"rls-{user_id}@example.com", "ss": str(uuid4())},
        )
        for tid in (tenant_a, tenant_b):
            await conn.execute(
                text(
                    "INSERT INTO tenants (id, slug, display_name, status, owner_user_id) "
                    "VALUES (:id, :slug, :dn, 'active', :owner)"
                ),
                {"id": str(tid), "slug": f"rls-{tid}", "dn": "t", "owner": str(user_id)},
            )
        await conn.execute(
            text(
                "INSERT INTO tenant_memberships "
                "(id, tenant_id, user_id, status, is_default, metadata, created_at, updated_at) "
                "VALUES (:id, :tid, :uid, 'active', false, '{}'::jsonb, now(), now())"
            ),
            {"id": str(uuid4()), "tid": str(tenant_a), "uid": str(user_id)},
        )
    return user_id, tenant_a, tenant_b


class TestDirectSqlLeak:
    """A query that explicitly asks for another tenant's rows must come back
    empty -- RLS, not the (absent, here) application filter, is what blocks it."""

    async def test_cannot_select_another_tenants_rows_by_id(
        self, engine: AsyncEngine, migrator_engine: AsyncEngine
    ) -> None:
        user_id, tenant_a, tenant_b = await _seed_user_and_two_tenants(migrator_engine)

        async with engine.connect() as conn, conn.begin():
            await conn.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant_a)}
            )
            result = await conn.execute(
                text("SELECT count(*) FROM tenant_memberships WHERE tenant_id = :b"),
                {"b": str(tenant_b)},
            )
            assert result.scalar() == 0

    async def test_cannot_select_another_tenants_row_by_slug(
        self, engine: AsyncEngine, migrator_engine: AsyncEngine
    ) -> None:
        _, tenant_a, tenant_b = await _seed_user_and_two_tenants(migrator_engine)

        async with engine.connect() as conn, conn.begin():
            await conn.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant_a)}
            )
            result = await conn.execute(text("SELECT slug FROM tenants"))
            slugs = [r[0] for r in result.all()]
            assert f"rls-{tenant_b}" not in slugs
            assert f"rls-{tenant_a}" in slugs


class TestNoContext:
    """No tenant context set at all => zero rows, not an error and not
    everything -- the fail-closed guarantee."""

    async def test_tenant_owned_tables_return_nothing_without_context(
        self, engine: AsyncEngine, migrator_engine: AsyncEngine
    ) -> None:
        await _seed_user_and_two_tenants(migrator_engine)

        async with engine.connect() as conn, conn.begin():
            # Deliberately never call set_config at all.
            result = await conn.execute(text("SELECT count(*) FROM tenant_memberships"))
            assert result.scalar() == 0
            result2 = await conn.execute(text("SELECT count(*) FROM tenants"))
            assert result2.scalar() == 0


class TestCrossTenantWrite:
    """WITH CHECK rejects a write claiming a tenant_id that doesn't match the
    active context, even though the acting user is real and authenticated."""

    async def test_insert_into_other_tenant_is_rejected(
        self, engine: AsyncEngine, migrator_engine: AsyncEngine
    ) -> None:
        user_id, tenant_a, tenant_b = await _seed_user_and_two_tenants(migrator_engine)

        async with engine.connect() as conn, conn.begin():
            await conn.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant_a)}
            )
            await conn.execute(
                text("SELECT set_config('app.user_id', :u, true)"), {"u": str(user_id)}
            )
            with pytest.raises(DBAPIError):
                await conn.execute(
                    text(
                        "INSERT INTO tenant_memberships "
                        "(id, tenant_id, user_id, status, is_default, metadata, created_at, updated_at) "
                        "VALUES (:id, :tid, :uid, 'active', false, '{}'::jsonb, now(), now())"
                    ),
                    {"id": str(uuid4()), "tid": str(tenant_b), "uid": str(user_id)},
                )

    async def test_update_of_tenants_table_is_rejected_entirely(
        self, engine: AsyncEngine, migrator_engine: AsyncEngine
    ) -> None:
        _, tenant_a, _ = await _seed_user_and_two_tenants(migrator_engine)

        async with engine.connect() as conn, conn.begin():
            await conn.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant_a)}
            )
            with pytest.raises(DBAPIError):
                await conn.execute(
                    text("UPDATE tenants SET display_name = 'hacked' WHERE id = :id"),
                    {"id": str(tenant_a)},
                )


class TestPoolReuse:
    """A connection that had app.tenant_id set in an earlier transaction must
    not leak that context (or a stale-empty-string cast error) into a later
    transaction that never re-sets it -- the NULLIF fix documented in
    docs/18-schema-rls-and-migrations.md.
    """

    async def test_reused_connection_does_not_error_or_leak_context(
        self, settings, migrator_engine: AsyncEngine
    ) -> None:
        from iam_platform.infrastructure.db.session import build_engine_from_dsn

        user_id, tenant_a, _ = await _seed_user_and_two_tenants(migrator_engine)

        # pool_size=1 forces the second checkout to reuse the same physical
        # connection as the first.
        single_conn_settings = settings.database.model_copy(
            update={"pool_size": 1, "pool_max_overflow": 0}
        )
        eng = build_engine_from_dsn(settings.database.async_dsn, single_conn_settings)
        try:
            async with eng.connect() as conn, conn.begin():
                await conn.execute(
                    text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant_a)}
                )
                result = await conn.execute(text("SELECT count(*) FROM tenants"))
                assert result.scalar() == 1  # sees only tenant_a

            # New transaction, same pooled connection, never re-sets tenant_id.
            async with eng.connect() as conn, conn.begin():
                result = await conn.execute(
                    text(
                        "SELECT NULLIF(current_setting('app.tenant_id', true), '')::uuid IS NULL"
                    )
                )
                assert result.scalar() is True  # normalized back to NULL, no cast error

                result2 = await conn.execute(text("SELECT count(*) FROM tenants"))
                assert result2.scalar() == 0  # fails closed, doesn't inherit tenant_a
        finally:
            await eng.dispose()


class TestPlatformBypassScope:
    """app_platform (BYPASSRLS) sees across every tenant; app_tenant cannot
    see platform-only tables at all, regardless of any tenant context."""

    async def test_app_platform_sees_all_tenants(
        self, platform_engine: AsyncEngine, migrator_engine: AsyncEngine
    ) -> None:
        _, tenant_a, tenant_b = await _seed_user_and_two_tenants(migrator_engine)

        async with platform_engine.connect() as conn, conn.begin():
            result = await conn.execute(
                text("SELECT id FROM tenants WHERE id IN (:a, :b)"),
                {"a": str(tenant_a), "b": str(tenant_b)},
            )
            seen = {str(r[0]) for r in result.all()}
            assert seen == {str(tenant_a), str(tenant_b)}

    async def test_app_tenant_cannot_query_platform_only_tables(
        self, engine: AsyncEngine, migrator_engine: AsyncEngine
    ) -> None:
        _, tenant_a, _ = await _seed_user_and_two_tenants(migrator_engine)

        async with engine.connect() as conn, conn.begin():
            await conn.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant_a)}
            )
            with pytest.raises(DBAPIError):
                await conn.execute(text("SELECT count(*) FROM platform_roles"))

    async def test_app_platform_can_query_platform_only_tables(
        self, platform_engine: AsyncEngine
    ) -> None:
        async with platform_engine.connect() as conn, conn.begin():
            result = await conn.execute(text("SELECT count(*) FROM platform_roles"))
            assert result.scalar() is not None  # no permission error, regardless of count
