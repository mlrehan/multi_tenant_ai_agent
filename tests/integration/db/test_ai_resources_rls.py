"""RLS proof suite for the Phase 7 AI-resource tables -- the same
database-level guarantees ``test_rls_isolation.py`` proves for the Phase 6
tables, extended to the tables that hold tenant knowledge and secrets.

Deliberately exercises Postgres directly rather than going through the
application layer: the point is that isolation holds even if application code
forgets to filter.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.integration

_TENANT_OWNED_TABLES = (
    "ai_assistants",
    "assistant_members",
    "knowledge_bases",
    "documents",
    "conversations",
    "provider_credentials",
)


async def _seed_two_tenants_with_assistants(migrator_engine: AsyncEngine):
    """Builds the minimum graph an assistant needs: user -> tenant ->
    membership -> model_configuration -> assistant, for two separate tenants.
    """
    user_id = uuid4()
    tenants = {}

    async with migrator_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO users (id, email, status, security_stamp) "
                "VALUES (:id, :email, 'active', :ss)"
            ),
            {"id": str(user_id), "email": f"ai-rls-{user_id}@example.com", "ss": str(uuid4())},
        )

        for label in ("a", "b"):
            tenant_id, membership_id, config_id, assistant_id, kb_id = (
                uuid4(),
                uuid4(),
                uuid4(),
                uuid4(),
                uuid4(),
            )
            await conn.execute(
                text(
                    "INSERT INTO tenants (id, slug, display_name, status, owner_user_id) "
                    "VALUES (:id, :slug, :dn, 'active', :owner)"
                ),
                {
                    "id": str(tenant_id),
                    "slug": f"ai-rls-{tenant_id}",
                    "dn": f"tenant-{label}",
                    "owner": str(user_id),
                },
            )
            await conn.execute(
                text(
                    "INSERT INTO tenant_memberships "
                    "(id, tenant_id, user_id, status, is_default, metadata, created_at, updated_at) "
                    "VALUES (:id, :tid, :uid, 'active', false, '{}'::jsonb, now(), now())"
                ),
                {"id": str(membership_id), "tid": str(tenant_id), "uid": str(user_id)},
            )
            await conn.execute(
                text(
                    "INSERT INTO model_configurations (id, tenant_id, model_name, parameters) "
                    "VALUES (:id, :tid, 'claude-sonnet-5', '{}'::jsonb)"
                ),
                {"id": str(config_id), "tid": str(tenant_id)},
            )
            # The entitlement `ai_assistants` now references. Owning a
            # configuration is no longer the same as being allowed to use it,
            # so the grant is a row this graph has to build explicitly --
            # see `tenant_model_configurations` in docs/16.
            await conn.execute(
                text(
                    "INSERT INTO tenant_model_configurations "
                    "(id, tenant_id, model_configuration_id) VALUES (:id, :tid, :cid)"
                ),
                {"id": str(uuid4()), "tid": str(tenant_id), "cid": str(config_id)},
            )
            await conn.execute(
                text(
                    "INSERT INTO ai_assistants "
                    "(id, tenant_id, name, visibility, owner_membership_id, "
                    " model_configuration_id, status) "
                    "VALUES (:id, :tid, :name, 'tenant', :mid, :cid, 'published')"
                ),
                {
                    "id": str(assistant_id),
                    "tid": str(tenant_id),
                    "name": f"assistant-{label}",
                    "mid": str(membership_id),
                    "cid": str(config_id),
                },
            )
            await conn.execute(
                text(
                    "INSERT INTO knowledge_bases "
                    "(id, tenant_id, name, owner_membership_id, visibility, vector_namespace) "
                    "VALUES (:id, :tid, :name, :mid, 'tenant', :ns)"
                ),
                {
                    "id": str(kb_id),
                    "tid": str(tenant_id),
                    "name": f"kb-{label}",
                    "mid": str(membership_id),
                    "ns": f"{tenant_id}/{kb_id}",
                },
            )
            await conn.execute(
                text(
                    "INSERT INTO provider_credentials "
                    "(id, owner_type, tenant_id, provider, credential_ciphertext, key_hint, "
                    " created_by_user_id) "
                    "VALUES (:id, 'tenant', :tid, 'anthropic', :ct, 'abcd', :uid)"
                ),
                {
                    "id": str(uuid4()),
                    "tid": str(tenant_id),
                    "ct": b"ciphertext",
                    "uid": str(user_id),
                },
            )
            tenants[label] = {
                "tenant_id": tenant_id,
                "membership_id": membership_id,
                "config_id": config_id,
                "assistant_id": assistant_id,
                "kb_id": kb_id,
            }

    return user_id, tenants


class TestAiResourceTenantIsolation:
    @pytest.mark.parametrize("table", _TENANT_OWNED_TABLES)
    async def test_no_context_returns_nothing(
        self, table: str, engine: AsyncEngine, migrator_engine: AsyncEngine
    ) -> None:
        """Fail-closed: with no tenant context set, every AI-resource table
        reads as empty rather than erroring or leaking."""
        await _seed_two_tenants_with_assistants(migrator_engine)

        async with engine.connect() as conn, conn.begin():
            result = await conn.execute(text(f"SELECT count(*) FROM {table}"))
            assert result.scalar() == 0

    async def test_cannot_see_another_tenants_assistants(
        self, engine: AsyncEngine, migrator_engine: AsyncEngine
    ) -> None:
        _, tenants = await _seed_two_tenants_with_assistants(migrator_engine)

        async with engine.connect() as conn, conn.begin():
            await conn.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"),
                {"t": str(tenants["a"]["tenant_id"])},
            )
            names = [r[0] for r in (await conn.execute(text("SELECT name FROM ai_assistants"))).all()]
            assert names == ["assistant-a"]

            # Even asking for the other tenant's row by primary key.
            result = await conn.execute(
                text("SELECT count(*) FROM ai_assistants WHERE id = :id"),
                {"id": str(tenants["b"]["assistant_id"])},
            )
            assert result.scalar() == 0

    async def test_cannot_see_another_tenants_knowledge_base_or_namespace(
        self, engine: AsyncEngine, migrator_engine: AsyncEngine
    ) -> None:
        """The vector namespace is the key to another tenant's embeddings --
        it must not be readable across the boundary."""
        _, tenants = await _seed_two_tenants_with_assistants(migrator_engine)

        async with engine.connect() as conn, conn.begin():
            await conn.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"),
                {"t": str(tenants["a"]["tenant_id"])},
            )
            namespaces = [
                r[0]
                for r in (await conn.execute(text("SELECT vector_namespace FROM knowledge_bases"))).all()
            ]
            assert namespaces == [f"{tenants['a']['tenant_id']}/{tenants['a']['kb_id']}"]

    async def test_cannot_read_another_tenants_provider_credential_ciphertext(
        self, engine: AsyncEngine, migrator_engine: AsyncEngine
    ) -> None:
        _, tenants = await _seed_two_tenants_with_assistants(migrator_engine)

        async with engine.connect() as conn, conn.begin():
            await conn.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"),
                {"t": str(tenants["a"]["tenant_id"])},
            )
            rows = (
                await conn.execute(
                    text("SELECT tenant_id FROM provider_credentials")
                )
            ).all()
            assert [str(r[0]) for r in rows] == [str(tenants["a"]["tenant_id"])]

    async def test_cross_tenant_assistant_insert_is_rejected(
        self, engine: AsyncEngine, migrator_engine: AsyncEngine
    ) -> None:
        _, tenants = await _seed_two_tenants_with_assistants(migrator_engine)

        async with engine.connect() as conn, conn.begin():
            await conn.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"),
                {"t": str(tenants["a"]["tenant_id"])},
            )
            with pytest.raises(DBAPIError):
                await conn.execute(
                    text(
                        "INSERT INTO ai_assistants "
                        "(id, tenant_id, name, visibility, owner_membership_id, "
                        " model_configuration_id, status) "
                        "VALUES (:id, :tid, 'smuggled', 'tenant', :mid, :cid, 'draft')"
                    ),
                    {
                        "id": str(uuid4()),
                        "tid": str(tenants["b"]["tenant_id"]),
                        "mid": str(tenants["b"]["membership_id"]),
                        "cid": str(tenants["b"]["config_id"]),
                    },
                )


class TestModelConfigurationNullableTenant:
    async def test_platform_defaults_are_readable_by_every_tenant(
        self, engine: AsyncEngine, migrator_engine: AsyncEngine
    ) -> None:
        _, tenants = await _seed_two_tenants_with_assistants(migrator_engine)
        platform_config_id = uuid4()
        async with migrator_engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO model_configurations (id, tenant_id, model_name, parameters) "
                    "VALUES (:id, NULL, 'platform-default-model', '{}'::jsonb)"
                ),
                {"id": str(platform_config_id)},
            )

        async with engine.connect() as conn, conn.begin():
            await conn.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"),
                {"t": str(tenants["a"]["tenant_id"])},
            )
            names = {
                r[0]
                for r in (await conn.execute(text("SELECT model_name FROM model_configurations"))).all()
            }
            assert "platform-default-model" in names

    async def test_tenant_cannot_see_another_tenants_model_configuration(
        self, engine: AsyncEngine, migrator_engine: AsyncEngine
    ) -> None:
        _, tenants = await _seed_two_tenants_with_assistants(migrator_engine)

        async with engine.connect() as conn, conn.begin():
            await conn.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"),
                {"t": str(tenants["a"]["tenant_id"])},
            )
            result = await conn.execute(
                text("SELECT count(*) FROM model_configurations WHERE id = :id"),
                {"id": str(tenants["b"]["config_id"])},
            )
            assert result.scalar() == 0

    async def test_tenant_cannot_write_a_platform_default(
        self, engine: AsyncEngine, migrator_engine: AsyncEngine
    ) -> None:
        """Readable but not writable -- the nullable-tenant split."""
        _, tenants = await _seed_two_tenants_with_assistants(migrator_engine)

        async with engine.connect() as conn, conn.begin():
            await conn.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"),
                {"t": str(tenants["a"]["tenant_id"])},
            )
            with pytest.raises(DBAPIError):
                await conn.execute(
                    text(
                        "INSERT INTO model_configurations (id, tenant_id, model_name, parameters) "
                        "VALUES (:id, NULL, 'rogue-default', '{}'::jsonb)"
                    ),
                    {"id": str(uuid4())},
                )


class TestProviderCredentialPlatformRows:
    async def test_platform_owned_credentials_are_invisible_to_tenants(
        self, engine: AsyncEngine, migrator_engine: AsyncEngine
    ) -> None:
        """Unlike model_configurations, a NULL-tenant provider credential is
        platform-only -- tenants must not see it at all (docs/16)."""
        user_id, tenants = await _seed_two_tenants_with_assistants(migrator_engine)
        async with migrator_engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO provider_credentials "
                    "(id, owner_type, tenant_id, provider, credential_ciphertext, key_hint, "
                    " created_by_user_id) "
                    "VALUES (:id, 'platform', NULL, 'anthropic', :ct, 'plat', :uid)"
                ),
                {"id": str(uuid4()), "ct": b"platform-ciphertext", "uid": str(user_id)},
            )

        async with engine.connect() as conn, conn.begin():
            await conn.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"),
                {"t": str(tenants["a"]["tenant_id"])},
            )
            result = await conn.execute(
                text("SELECT count(*) FROM provider_credentials WHERE tenant_id IS NULL")
            )
            assert result.scalar() == 0
