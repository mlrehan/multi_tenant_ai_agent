"""RLS proof for ``document_chunks`` -- the table Phase 11 added.

Every other tenant-owned table has one of these (``test_rls_isolation.py``,
``test_ai_resources_rls.py``); a new table without one is a table whose
isolation is asserted only by the migration having been written correctly.
These run as ``app_tenant`` against live Postgres, so they prove the
*database* refuses cross-tenant access independently of whether application
code remembers to filter.

Chunks are the one place a tenant's document *text* is stored in Postgres, so
a leak here is a leak of content, not just metadata.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def two_tenants(migrator_engine: AsyncEngine) -> AsyncIterator[dict[str, UUID]]:
    """Two tenants, each with a knowledge base, a document, and one chunk."""
    ids: dict[str, UUID] = {
        "user_id": uuid4(),
        "tenant_a": uuid4(),
        "tenant_b": uuid4(),
    }
    for suffix in ("a", "b"):
        ids[f"membership_{suffix}"] = uuid4()
        ids[f"kb_{suffix}"] = uuid4()
        ids[f"document_{suffix}"] = uuid4()
        ids[f"chunk_{suffix}"] = uuid4()

    async with migrator_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO users (id, email, status, security_stamp) "
                "VALUES (:id, :email, 'active', :stamp)"
            ),
            {"id": str(ids["user_id"]), "email": f"chunks-{ids['user_id']}@example.test",
             "stamp": str(uuid4())},
        )
        for suffix in ("a", "b"):
            tenant_id = ids[f"tenant_{suffix}"]
            await conn.execute(
                text(
                    "INSERT INTO tenants (id, slug, display_name, status, owner_user_id) "
                    "VALUES (:id, :slug, 'Chunk Test', 'active', :owner)"
                ),
                {"id": str(tenant_id), "slug": f"chunks-{tenant_id}",
                 "owner": str(ids["user_id"])},
            )
            await conn.execute(
                text(
                    "INSERT INTO tenant_memberships "
                    "(id, tenant_id, user_id, status, is_default, metadata, "
                    " created_at, updated_at, joined_at) "
                    "VALUES (:id, :tid, :uid, 'active', false, '{}'::jsonb, "
                    "        now(), now(), now())"
                ),
                {"id": str(ids[f"membership_{suffix}"]), "tid": str(tenant_id),
                 "uid": str(ids["user_id"])},
            )
            await conn.execute(
                text(
                    "INSERT INTO knowledge_bases "
                    "(id, tenant_id, name, owner_membership_id, visibility, vector_namespace) "
                    "VALUES (:id, :tid, 'KB', :m, 'tenant', :ns)"
                ),
                {"id": str(ids[f"kb_{suffix}"]), "tid": str(tenant_id),
                 "m": str(ids[f"membership_{suffix}"]),
                 "ns": f"{tenant_id}/{ids[f'kb_{suffix}']}"},
            )
            await conn.execute(
                text(
                    "INSERT INTO documents "
                    "(id, tenant_id, knowledge_base_id, uploaded_by_membership_id, "
                    " filename, content_type, storage_path, size_bytes, status, checksum) "
                    "VALUES (:id, :tid, :kb, :m, 'f.csv', 'text/csv', :path, 10, "
                    "        'ready', 'abc')"
                ),
                {"id": str(ids[f"document_{suffix}"]), "tid": str(tenant_id),
                 "kb": str(ids[f"kb_{suffix}"]), "m": str(ids[f"membership_{suffix}"]),
                 "path": f"{tenant_id}/{ids[f'kb_{suffix}']}/{ids[f'document_{suffix}']}"},
            )
            await conn.execute(
                text(
                    "INSERT INTO document_chunks "
                    "(id, tenant_id, knowledge_base_id, document_id, chunk_index, "
                    " content, token_count, source_location) "
                    "VALUES (:id, :tid, :kb, :doc, 0, :content, 3, 'row 2')"
                ),
                {"id": str(ids[f"chunk_{suffix}"]), "tid": str(tenant_id),
                 "kb": str(ids[f"kb_{suffix}"]), "doc": str(ids[f"document_{suffix}"]),
                 "content": f"secret content for tenant {suffix}"},
            )

    yield ids

    async with migrator_engine.begin() as conn:
        for suffix in ("a", "b"):
            await conn.execute(
                text("DELETE FROM document_chunks WHERE tenant_id = :t"),
                {"t": str(ids[f"tenant_{suffix}"])},
            )
            await conn.execute(
                text("DELETE FROM documents WHERE tenant_id = :t"),
                {"t": str(ids[f"tenant_{suffix}"])},
            )
            await conn.execute(
                text("DELETE FROM knowledge_bases WHERE tenant_id = :t"),
                {"t": str(ids[f"tenant_{suffix}"])},
            )
            await conn.execute(
                text("DELETE FROM tenant_memberships WHERE tenant_id = :t"),
                {"t": str(ids[f"tenant_{suffix}"])},
            )
        await conn.execute(
            text("DELETE FROM tenants WHERE id = ANY(:ids)"),
            {"ids": [str(ids["tenant_a"]), str(ids["tenant_b"])]},
        )
        await conn.execute(
            text("DELETE FROM users WHERE id = :u"), {"u": str(ids["user_id"])}
        )


async def _rows_visible_as(engine: AsyncEngine, tenant_id: UUID | None) -> list[tuple]:
    async with AsyncSession(engine) as session, session.begin():
        if tenant_id is not None:
            await session.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant_id)}
            )
        result = await session.execute(text("SELECT id, content FROM document_chunks"))
        return list(result.all())


class TestDocumentChunksIsolation:
    async def test_a_tenant_sees_only_its_own_chunks(
        self, engine: AsyncEngine, two_tenants: dict[str, UUID]
    ) -> None:
        rows = await _rows_visible_as(engine, two_tenants["tenant_a"])

        ids = {row[0] for row in rows}
        assert two_tenants["chunk_a"] in ids
        assert two_tenants["chunk_b"] not in ids

    async def test_chunk_text_does_not_leak_across_tenants(
        self, engine: AsyncEngine, two_tenants: dict[str, UUID]
    ) -> None:
        """`document_chunks.content` is the one place a tenant's document text
        lives in Postgres -- a leak here is content, not just metadata."""
        rows = await _rows_visible_as(engine, two_tenants["tenant_a"])

        contents = " ".join(row[1] for row in rows)
        assert "tenant a" in contents
        assert "tenant b" not in contents

    async def test_no_tenant_context_returns_nothing(
        self, engine: AsyncEngine, two_tenants: dict[str, UUID]
    ) -> None:
        """Fail closed. A transaction that forgets to set context must see
        zero rows, not every row."""
        assert await _rows_visible_as(engine, None) == []

    async def test_an_explicit_cross_tenant_filter_still_returns_nothing(
        self, engine: AsyncEngine, two_tenants: dict[str, UUID]
    ) -> None:
        """RLS is independently sufficient, not merely redundant with the
        application's own WHERE clause -- so a query that *deliberately* asks
        for another tenant's rows still gets none."""
        async with AsyncSession(engine) as session, session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"),
                {"t": str(two_tenants["tenant_a"])},
            )
            result = await session.execute(
                text("SELECT id FROM document_chunks WHERE tenant_id = :other"),
                {"other": str(two_tenants["tenant_b"])},
            )
            assert result.all() == []

    async def test_cross_tenant_insert_is_rejected_by_with_check(
        self, engine: AsyncEngine, two_tenants: dict[str, UUID]
    ) -> None:
        """Rejected at write time, not silently filtered at read time."""
        with pytest.raises(Exception, match="row-level security|violates"):
            async with AsyncSession(engine) as session, session.begin():
                await session.execute(
                    text("SELECT set_config('app.tenant_id', :t, true)"),
                    {"t": str(two_tenants["tenant_a"])},
                )
                await session.execute(
                    text(
                        "INSERT INTO document_chunks "
                        "(id, tenant_id, knowledge_base_id, document_id, chunk_index, "
                        " content, token_count) "
                        "VALUES (:id, :tid, :kb, :doc, 99, 'injected', 1)"
                    ),
                    {
                        "id": str(uuid4()),
                        # Tenant B's id, while the session is scoped to A.
                        "tid": str(two_tenants["tenant_b"]),
                        "kb": str(two_tenants["kb_b"]),
                        "doc": str(two_tenants["document_b"]),
                    },
                )

    async def test_deleting_a_document_cascades_to_its_chunks(
        self, engine: AsyncEngine, migrator_engine: AsyncEngine, two_tenants: dict[str, UUID]
    ) -> None:
        """Chunks are derived data with no independent meaning -- an orphaned
        chunk would keep a deleted document's text searchable."""
        async with migrator_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM documents WHERE id = :d"),
                {"d": str(two_tenants["document_a"])},
            )

        rows = await _rows_visible_as(engine, two_tenants["tenant_a"])
        assert rows == []
