"""RLS proof for `data_sources` -- the Phase 12 table.

Exercises Postgres directly rather than the application layer, for the same
reason as the other proof suites: the point is that isolation holds even if
application code forgets to filter.

**What a leak here would cost.** A crawl source holds the URLs a tenant pointed
this platform at. Those are not neutral: an internal wiki address, a staging
host, a customer portal on a guessable domain. Reading another tenant's sources
maps their infrastructure without touching a single indexed document, so this
table needs the same isolation as the content it produces.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.integration


async def _seed_two_tenants_with_sources(migrator_engine: AsyncEngine):
    """user -> tenant -> membership -> knowledge_base -> data_source, twice."""
    user_id = uuid4()
    tenants: dict[str, dict[str, object]] = {}

    async with migrator_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO users (id, email, status, security_stamp) "
                "VALUES (:id, :email, 'active', :ss)"
            ),
            {"id": str(user_id), "email": f"ds-rls-{user_id}@example.com", "ss": str(uuid4())},
        )

        for label in ("a", "b"):
            tenant_id, membership_id, kb_id, source_id = uuid4(), uuid4(), uuid4(), uuid4()
            await conn.execute(
                text(
                    "INSERT INTO tenants (id, slug, display_name, status, owner_user_id) "
                    "VALUES (:id, :slug, :name, 'active', :owner)"
                ),
                {
                    "id": str(tenant_id),
                    "slug": f"ds-rls-{tenant_id}",
                    "name": f"tenant-{label}",
                    "owner": str(user_id),
                },
            )
            await conn.execute(
                text(
                    "INSERT INTO tenant_memberships "
                    "(id, tenant_id, user_id, status, is_default, metadata, "
                    " created_at, updated_at, joined_at) "
                    "VALUES (:id, :tid, :uid, 'active', false, '{}'::jsonb, "
                    "        now(), now(), now())"
                ),
                {"id": str(membership_id), "tid": str(tenant_id), "uid": str(user_id)},
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
                    "INSERT INTO data_sources "
                    "(id, tenant_id, knowledge_base_id, kind, config, "
                    " created_by_membership_id) "
                    "VALUES (:id, :tid, :kbid, 'url_crawl', CAST(:cfg AS jsonb), :mid)"
                ),
                {
                    "id": str(source_id),
                    "tid": str(tenant_id),
                    "kbid": str(kb_id),
                    "cfg": f'{{"urls": ["https://{label}-internal.example.test/"], "mode": "site"}}',
                    "mid": str(membership_id),
                },
            )
            tenants[label] = {
                "tenant_id": tenant_id,
                "membership_id": membership_id,
                "kb_id": kb_id,
                "source_id": source_id,
            }

    return user_id, tenants


class TestDataSourceIsolation:
    async def test_no_context_returns_nothing(
        self, engine: AsyncEngine, migrator_engine: AsyncEngine
    ) -> None:
        """Fail-closed: with no tenant context, the table reads as empty rather
        than erroring or leaking."""
        await _seed_two_tenants_with_sources(migrator_engine)

        async with engine.connect() as conn, conn.begin():
            result = await conn.execute(text("SELECT count(*) FROM data_sources"))
            assert result.scalar() == 0

    async def test_cannot_see_another_tenants_crawl_targets(
        self, engine: AsyncEngine, migrator_engine: AsyncEngine
    ) -> None:
        _, tenants = await _seed_two_tenants_with_sources(migrator_engine)

        async with engine.connect() as conn, conn.begin():
            await conn.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"),
                {"t": str(tenants["a"]["tenant_id"])},
            )
            rows = (
                await conn.execute(text("SELECT config ->> 'urls' FROM data_sources"))
            ).all()
            assert len(rows) == 1
            assert "a-internal" in rows[0][0]
            # The other tenant's URLs must not appear anywhere in the result.
            assert "b-internal" not in rows[0][0]

            # Not even by primary key.
            result = await conn.execute(
                text("SELECT count(*) FROM data_sources WHERE id = :id"),
                {"id": str(tenants["b"]["source_id"])},
            )
            assert result.scalar() == 0

    async def test_an_explicit_cross_tenant_filter_still_returns_nothing(
        self, engine: AsyncEngine, migrator_engine: AsyncEngine
    ) -> None:
        """Naming the other tenant in the WHERE clause does not help: the
        policy is applied on top of, not instead of, the query's own filter."""
        _, tenants = await _seed_two_tenants_with_sources(migrator_engine)

        async with engine.connect() as conn, conn.begin():
            await conn.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"),
                {"t": str(tenants["a"]["tenant_id"])},
            )
            result = await conn.execute(
                text("SELECT count(*) FROM data_sources WHERE tenant_id = :other"),
                {"other": str(tenants["b"]["tenant_id"])},
            )
            assert result.scalar() == 0

    async def test_cross_tenant_insert_is_rejected(
        self, engine: AsyncEngine, migrator_engine: AsyncEngine
    ) -> None:
        """`WITH CHECK`: a tenant cannot plant a crawl source in another
        tenant's knowledge base -- which would otherwise let them have this
        platform fetch URLs on someone else's behalf, and index the results
        where that tenant would read them."""
        _, tenants = await _seed_two_tenants_with_sources(migrator_engine)

        async with engine.connect() as conn, conn.begin():
            await conn.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"),
                {"t": str(tenants["a"]["tenant_id"])},
            )
            with pytest.raises(DBAPIError):
                await conn.execute(
                    text(
                        "INSERT INTO data_sources "
                        "(id, tenant_id, knowledge_base_id, kind, config, "
                        " created_by_membership_id) "
                        "VALUES (:id, :tid, :kbid, 'url_crawl', "
                        "        CAST('{\"urls\": [\"https://x.test/\"]}' AS jsonb), :mid)"
                    ),
                    {
                        "id": str(uuid4()),
                        "tid": str(tenants["b"]["tenant_id"]),
                        "kbid": str(tenants["b"]["kb_id"]),
                        "mid": str(tenants["b"]["membership_id"]),
                    },
                )

    async def test_own_tenant_rows_remain_writable(
        self, engine: AsyncEngine, migrator_engine: AsyncEngine
    ) -> None:
        """The positive control. Without it, a policy that rejected *every*
        write would pass the test above while breaking the feature."""
        _, tenants = await _seed_two_tenants_with_sources(migrator_engine)

        async with engine.connect() as conn, conn.begin():
            await conn.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"),
                {"t": str(tenants["a"]["tenant_id"])},
            )
            await conn.execute(
                text(
                    "UPDATE data_sources SET sync_status = 'ready' WHERE id = :id"
                ),
                {"id": str(tenants["a"]["source_id"])},
            )
            status = (
                await conn.execute(
                    text("SELECT sync_status FROM data_sources WHERE id = :id"),
                    {"id": str(tenants["a"]["source_id"])},
                )
            ).scalar()
            assert status == "ready"


class TestUrlCrawlConstraint:
    async def test_a_url_crawl_with_no_urls_cannot_be_stored(
        self, migrator_engine: AsyncEngine
    ) -> None:
        """The domain entity refuses this too. The CHECK constraint means a
        fixture, a migration or a hand-written INSERT cannot bypass it -- a row
        that can never do anything should not be storable at all."""
        _, tenants = await _seed_two_tenants_with_sources(migrator_engine)

        async with migrator_engine.connect() as conn, conn.begin():
            with pytest.raises(DBAPIError):
                await conn.execute(
                    text(
                        "INSERT INTO data_sources "
                        "(id, tenant_id, knowledge_base_id, kind, config, "
                        " created_by_membership_id) "
                        "VALUES (:id, :tid, :kbid, 'url_crawl', "
                        "        CAST('{\"urls\": []}' AS jsonb), :mid)"
                    ),
                    {
                        "id": str(uuid4()),
                        "tid": str(tenants["a"]["tenant_id"]),
                        "kbid": str(tenants["a"]["kb_id"]),
                        "mid": str(tenants["a"]["membership_id"]),
                    },
                )
