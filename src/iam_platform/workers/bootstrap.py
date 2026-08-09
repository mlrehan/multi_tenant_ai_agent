"""Worker composition root -- builds services without FastAPI's DI.

Separate from the API's ``iam_platform/bootstrap.py`` because the two
processes need genuinely different things: the worker has no HTTP client, no
JWT service, no OAuth providers, no rate limiter, and it *does* need the
object-storage client and embedding client the API doesn't (yet) use. Sharing
one builder would mean each process constructing half a container it never
touches -- including opening connection pools nothing reads from.

Reuses ``infrastructure.factories``' selection helpers (``build_secret_provider``,
``build_object_storage_client``, ``build_vector_stack``) rather than
duplicating the "which adapter does this setting mean" logic, so a new storage
backend is wired in one place, not two.

Those helpers live in ``infrastructure`` rather than in the API's
``bootstrap.py`` for a reason import-linter had to point out: importing
anything from ``bootstrap.py`` drags in ``api.main`` and ``api.deps.container``
transitively, which would put the entire FastAPI app in the worker's import
graph and break the ``workers`` ↛ ``api`` layering rule.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from iam_platform.application.ai_resources.ports import (
    EmbeddingClient,
    ObjectStorageClient,
    VectorSearchClient,
)
from iam_platform.core.clock import Clock, SystemClock
from iam_platform.core.config import Settings
from iam_platform.infrastructure.db.session import build_engine, build_session_factory
from iam_platform.infrastructure.factories import (
    build_object_storage_client,
    build_secret_provider,
    build_vector_stack,
)
from iam_platform.infrastructure.secrets.resolver import resolve_secrets

logger = logging.getLogger("iam_platform.workers.bootstrap")


@dataclass(slots=True)
class WorkerContainer:
    settings: Settings
    engine: AsyncEngine
    # Matches `build_session_factory`'s declared return type -- the concrete
    # object is an `async_sessionmaker`, but the narrower callable type is what
    # the rest of the codebase passes around.
    session_factory: Callable[[], AsyncSession]
    object_storage: ObjectStorageClient
    vector_search: VectorSearchClient
    embedding_client: EmbeddingClient | None
    clock: Clock

    async def shutdown(self) -> None:
        """Disposes the connection pool.

        Phase 9 found that nothing in the API was ever disposed, so terminating
        pods held Postgres connections until the server timed them out. A
        worker pool is no different -- and worker processes restart more often
        than API pods do.
        """
        try:
            await self.engine.dispose()
        except Exception:
            logger.exception("failed to dispose worker engine during shutdown")


async def build_worker_container(settings: Settings | None = None) -> WorkerContainer:
    resolved = settings or Settings()
    # Same `secret://` resolution the API does -- a worker reading a literal
    # "secret://prod/db/password" as its password is the identical failure
    # Phase 9 found on the API side.
    resolved = await resolve_secrets(resolved, build_secret_provider(resolved))

    # `app_tenant` only. Workers never connect as `app_platform`
    # (docs/18-schema-rls-and-migrations.md: "Application code and worker code
    # only ever connect as app_tenant") -- a BYPASSRLS connection in a
    # background job would make the per-job tenant context decorative.
    engine = build_engine(resolved.database)
    vector_search, embedding_client = build_vector_stack(resolved)

    if embedding_client is None:
        # Loud, because a worker whose whole purpose is embedding cannot do
        # its job. It still starts: other job types may exist, and a worker
        # that refuses to boot takes down its queue entirely.
        logger.error(
            "OPENAI__API_KEY is not set -- document ingestion jobs will fail. "
            "The worker will start, but every ingestion task will error until it is configured."
        )

    return WorkerContainer(
        settings=resolved,
        engine=engine,
        session_factory=build_session_factory(engine),
        object_storage=build_object_storage_client(resolved),
        vector_search=vector_search,
        embedding_client=embedding_client,
        clock=SystemClock(),
    )
