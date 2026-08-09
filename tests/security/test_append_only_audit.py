"""Guards the STRIDE repudiation mitigation: audit tables are append-only for
both application roles (docs/03-threat-model.md).

This regressed once already -- ``ALTER DEFAULT PRIVILEGES`` in
docker/postgres-init/01-roles.sql grants full CRUD on every newly created
table, so any future table re-creation silently re-opens the hole. These tests
are the tripwire.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.integration

_APPEND_ONLY_TABLES = ("audit_logs", "security_events")


async def _seed_audit_row(migrator_engine: AsyncEngine) -> None:
    async with migrator_engine.begin() as conn:
        await conn.execute(
            text(
                # `metadata` is NOT NULL with a Python-side ORM default only,
                # so a raw INSERT has to supply it explicitly.
                "INSERT INTO audit_logs (id, actor_user_id, action, result, metadata) "
                "VALUES (:id, NULL, 'test.action', 'success', '{}'::jsonb)"
            ),
            {"id": str(uuid4())},
        )


class TestAuditTablesAreAppendOnly:
    @pytest.mark.parametrize("table", _APPEND_ONLY_TABLES)
    @pytest.mark.parametrize("role", ["app_tenant", "app_platform"])
    async def test_no_update_or_delete_grant(
        self, table: str, role: str, migrator_engine: AsyncEngine
    ) -> None:
        async with migrator_engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT privilege_type FROM information_schema.role_table_grants "
                    "WHERE table_name = :t AND grantee = :r"
                ),
                {"t": table, "r": role},
            )
            granted = {row[0] for row in result.all()}

        assert "UPDATE" not in granted, f"{role} can rewrite {table} history"
        assert "DELETE" not in granted, f"{role} can erase {table} history"
        # INSERT must survive -- the app has to be able to *write* audit rows.
        assert "INSERT" in granted, f"{role} cannot write {table} at all"

    async def test_app_role_cannot_delete_an_audit_row(
        self, engine: AsyncEngine, migrator_engine: AsyncEngine
    ) -> None:
        """The grant check above is the mechanism; this is the behaviour."""
        await _seed_audit_row(migrator_engine)

        async with engine.connect() as conn, conn.begin():
            with pytest.raises(DBAPIError):
                await conn.execute(text("DELETE FROM audit_logs"))

    async def test_app_role_cannot_update_an_audit_row(
        self, engine: AsyncEngine, migrator_engine: AsyncEngine
    ) -> None:
        await _seed_audit_row(migrator_engine)

        async with engine.connect() as conn, conn.begin():
            with pytest.raises(DBAPIError):
                await conn.execute(text("UPDATE audit_logs SET action = 'tampered'"))

    async def test_app_role_can_still_insert(self, engine: AsyncEngine) -> None:
        """The mitigation must not have broken the thing it protects."""
        async with engine.connect() as conn, conn.begin():
            await conn.execute(
                text(
                    "INSERT INTO audit_logs (id, actor_user_id, action, result, metadata) "
                    "VALUES (:id, NULL, 'test.insert_allowed', 'success', '{}'::jsonb)"
                ),
                {"id": str(uuid4())},
            )
