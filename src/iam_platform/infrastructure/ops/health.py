"""Concrete readiness probes -- docs/22-deployment-and-operations.md.

**Why this exists (Phase 9 finding).** ``/readyz`` previously returned a hard
-coded ``{"status": "ready"}``. Under Kubernetes that means a pod whose
database connection is dead still passes its readiness gate and keeps
receiving traffic, turning a recoverable dependency blip into a stream of
500s. A readiness probe that cannot fail is worse than none, because it
actively suppresses the orchestrator's ability to route around the problem.

Probes are bounded by a timeout and run concurrently: readiness is called
frequently (every few seconds per pod) and must not itself become a load
source or hang the event loop when a dependency is slow rather than down.
"""

from __future__ import annotations

import asyncio
import logging

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from iam_platform.application.ops.ports import DependencyStatus, HealthReport

logger = logging.getLogger("iam_platform.health")

# Sized for a *cold* connection pool, not a warm one. Measured on the dev
# stack: the first Postgres connect (TCP + auth) takes ~2.5s and the first
# Redis ping ~2.2s, while subsequent ones are ~0.1s and ~0.002s. A 2s budget
# therefore fails the very first readiness probe of every freshly started pod
# and reports a misleading "timeout" -- so the bound has to cover the cold
# case while staying well under a typical 10s probe interval.
_PROBE_TIMEOUT_SECONDS = 5.0


async def _probe_postgres(engine: AsyncEngine) -> DependencyStatus:
    try:
        async with asyncio.timeout(_PROBE_TIMEOUT_SECONDS):
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        return DependencyStatus(name="postgres", healthy=True)
    except TimeoutError:
        return DependencyStatus(name="postgres", healthy=False, detail="timeout")
    except Exception:
        # Logged with the real exception for operators; the response carries
        # only a generic label, since /readyz is typically unauthenticated.
        logger.exception("postgres readiness probe failed")
        return DependencyStatus(name="postgres", healthy=False, detail="unavailable")


async def _probe_redis(redis: Redis) -> DependencyStatus:
    try:
        async with asyncio.timeout(_PROBE_TIMEOUT_SECONDS):
            await redis.ping()
        return DependencyStatus(name="redis", healthy=True)
    except TimeoutError:
        return DependencyStatus(name="redis", healthy=False, detail="timeout")
    except Exception:
        logger.exception("redis readiness probe failed")
        return DependencyStatus(name="redis", healthy=False, detail="unavailable")


class DependencyHealthCheck:
    """Implements ``application.ops.ports.HealthCheck``.

    Both engines are probed, not just the tenant one: the platform
    (BYPASSRLS) connection uses a different role and different credentials, so
    it can fail independently -- a pod that can serve tenant traffic but not
    platform traffic is still degraded, and readiness should say so.
    """

    def __init__(
        self, *, engine: AsyncEngine, platform_engine: AsyncEngine, redis: Redis
    ) -> None:
        self._engine = engine
        self._platform_engine = platform_engine
        self._redis = redis

    async def check(self) -> HealthReport:
        tenant_db, platform_db, redis_status = await asyncio.gather(
            _probe_postgres(self._engine),
            _probe_postgres(self._platform_engine),
            _probe_redis(self._redis),
        )
        return HealthReport(
            dependencies=[
                DependencyStatus(
                    name="postgres_tenant",
                    healthy=tenant_db.healthy,
                    detail=tenant_db.detail,
                ),
                DependencyStatus(
                    name="postgres_platform",
                    healthy=platform_db.healthy,
                    detail=platform_db.detail,
                ),
                redis_status,
            ]
        )
