"""Async engine/session factory -- docs/18-schema-rls-and-migrations.md session-per-request pattern."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from iam_platform.core.config import DatabaseSettings


def build_engine_from_dsn(dsn: str, settings: DatabaseSettings) -> AsyncEngine:
    return create_async_engine(
        dsn,
        pool_size=settings.pool_size,
        max_overflow=settings.pool_max_overflow,
        pool_pre_ping=True,  # detect a dead pooled connection before using it, not after
    )


def build_engine(settings: DatabaseSettings) -> AsyncEngine:
    """The application's normal runtime connection -- RLS-subject `app_tenant`."""
    return build_engine_from_dsn(settings.async_dsn, settings)


def build_platform_engine(settings: DatabaseSettings) -> AsyncEngine:
    """BYPASSRLS `app_platform` connection -- Platform Service Layer only."""
    return build_engine_from_dsn(settings.platform_dsn, settings)


def build_session_factory(engine: AsyncEngine) -> Callable[[], AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
