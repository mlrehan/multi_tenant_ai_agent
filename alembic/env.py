"""Alembic environment -- async engine, DSN sourced from ``core.config.Settings``
(never hardcoded here), full model metadata imported for autogenerate.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from iam_platform.core.config import Settings
from iam_platform.infrastructure.db.base import Base

# Imported for their side effect of registering tables on Base.metadata --
# required for autogenerate to see them.
from iam_platform.infrastructure.db.models import ai_resources as _ai_resources_models  # noqa: F401
from iam_platform.infrastructure.db.models import audit as _audit_models  # noqa: F401
from iam_platform.infrastructure.db.models import identity as _identity_models  # noqa: F401
from iam_platform.infrastructure.db.models import platform_authz as _platform_authz_models  # noqa: F401,E501
from iam_platform.infrastructure.db.models import tenancy as _tenancy_models  # noqa: F401
from iam_platform.infrastructure.db.models import tenant_authz as _tenant_authz_models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url() -> str:
    settings = Settings()  # type: ignore[call-arg]  -- populated from env/.env
    # Migrations run as the table-owning role, never the RLS-subject app role
    # -- see docs/18-schema-rls-and-migrations.md.
    return settings.database.migrator_dsn


def run_migrations_offline() -> None:
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = get_url()
    connectable = async_engine_from_config(
        configuration, prefix="sqlalchemy.", poolclass=pool.NullPool
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
