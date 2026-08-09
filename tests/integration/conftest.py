"""Fixtures for tests that hit a real Postgres/Redis -- see tests/integration/README.md.

Reads connection settings the same way the app does (``Settings()`` from
env/``.env``), so these tests exercise the exact DSN/pooling configuration
production code uses, not a hand-rolled test-only connection string.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from iam_platform.application.platform_authz.ports import PlatformUnitOfWork
from iam_platform.application.tenant_authz.ports import TenantUnitOfWork
from iam_platform.core.config import Settings
from iam_platform.infrastructure.db.base import Base
from iam_platform.infrastructure.db.models import audit as _audit_models  # noqa: F401
from iam_platform.infrastructure.db.models import identity as _identity_models  # noqa: F401
from iam_platform.infrastructure.db.models import platform_authz as _platform_authz_models  # noqa: F401,E501
from iam_platform.infrastructure.db.models import tenancy as _tenancy_models  # noqa: F401
from iam_platform.infrastructure.db.models import tenant_authz as _tenant_authz_models  # noqa: F401
from iam_platform.infrastructure.db.session import (
    build_engine,
    build_engine_from_dsn,
    build_platform_engine,
    build_session_factory,
)
from iam_platform.infrastructure.db.unit_of_work import (
    SqlIdentityUnitOfWork,
    SqlPlatformUnitOfWork,
    SqlTenantUnitOfWork,
)

_ALL_TABLES = [t.name for t in Base.metadata.sorted_tables]


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


@pytest_asyncio.fixture
async def engine(settings: Settings) -> AsyncIterator[AsyncEngine]:
    # Function-scoped (not session-scoped): an asyncpg connection pool is
    # bound to the event loop it was created on, and pytest-asyncio's default
    # fixture loop scope is per-test -- a session-scoped engine would try to
    # reuse pooled connections across a closed event loop on the second test
    # (surfaced by integration testing as "Event loop is closed"). Rebuilding
    # per test is the standard, robust pattern for async SQLAlchemy + pytest.
    eng = build_engine(settings.database)
    try:
        yield eng
    finally:
        # TRUNCATE requires table-owner privilege, which the RLS-subject
        # app_tenant role deliberately does NOT have (least privilege) --
        # teardown uses the migrator connection instead, same as a real
        # maintenance job would (docs/18-schema-rls-and-migrations.md).
        migrator_engine = build_engine_from_dsn(settings.database.migrator_dsn, settings.database)
        try:
            async with migrator_engine.begin() as conn:
                table_list = ", ".join(_ALL_TABLES)
                await conn.execute(text(f"TRUNCATE {table_list} RESTART IDENTITY CASCADE"))
        finally:
            await migrator_engine.dispose()
        await eng.dispose()


@pytest_asyncio.fixture
async def platform_engine(settings: Settings) -> AsyncIterator[AsyncEngine]:
    eng = build_platform_engine(settings.database)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest_asyncio.fixture
async def migrator_engine(settings: Settings) -> AsyncIterator[AsyncEngine]:
    """Table-owning role -- bypasses RLS entirely (superuser). Used only to
    seed/inspect fixture data directly, never by application code."""
    eng = build_engine_from_dsn(settings.database.migrator_dsn, settings.database)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest.fixture
def uow_factory(engine: AsyncEngine) -> Callable[[], SqlIdentityUnitOfWork]:
    session_factory = build_session_factory(engine)
    return lambda: SqlIdentityUnitOfWork(session_factory)


@pytest.fixture
def tenant_uow_factory(engine: AsyncEngine) -> Callable[[UUID, UUID | None], TenantUnitOfWork]:
    session_factory = build_session_factory(engine)
    return lambda user_id, tenant_id: SqlTenantUnitOfWork(
        session_factory, user_id=user_id, tenant_id=tenant_id
    )


@pytest.fixture
def platform_uow_factory(platform_engine: AsyncEngine) -> Callable[[UUID], PlatformUnitOfWork]:
    session_factory = build_session_factory(platform_engine)
    return lambda user_id: SqlPlatformUnitOfWork(session_factory, user_id=user_id)
