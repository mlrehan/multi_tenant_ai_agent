"""Live ingestion progress, in Redis.

The ingestion job runs in one database transaction from start to finish, so
anything it wrote to its `documents` row mid-way would stay invisible until
the job committed -- which is when progress no longer matters. Progress is
also transient: it only means something while a file is in flight, and the
authoritative outcome (ready, or failed with a reason) is already on the row.

So it lives here: one small JSON value per document, written by the worker as
each stage starts, expiring on its own. **Both sides fail open.** A report
that cannot be written must not fail an ingestion, and a read that cannot be
made shows "processing" instead of a stage -- the console loses its live
detail, never a document.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from uuid import UUID

from redis.asyncio import Redis

from iam_platform.application.ai_resources.ports import IngestionProgress, IngestionStage

logger = logging.getLogger("iam_platform.infrastructure.cache.ingestion_progress")

#: Long enough to outlive any real ingestion (docling on a large scan takes
#: minutes) and to keep the stage a failure happened at visible for a while
#: after it; short enough that nothing lingers.
PROGRESS_TTL_SECONDS = 60 * 60


def _key(tenant_id: UUID, document_id: UUID) -> str:
    # The tenant is part of the key, not only the document: a reader has to
    # name the tenant it was authorized for, so one tenant's session can never
    # read another's progress by guessing a document id.
    return f"ingest-progress:{tenant_id}:{document_id}"


class RedisIngestionProgressStore:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def report(
        self,
        *,
        tenant_id: UUID,
        document_id: UUID,
        stage: IngestionStage,
        percent: int,
        detail: str | None = None,
    ) -> None:
        value = json.dumps(
            {"stage": stage.value, "percent": max(0, min(100, int(percent))), "detail": detail}
        )
        try:
            await self._redis.set(_key(tenant_id, document_id), value, ex=PROGRESS_TTL_SECONDS)
        except Exception:
            logger.warning("could not record ingestion progress for %s", document_id)

    async def read_many(
        self, *, tenant_id: UUID, document_ids: Sequence[UUID]
    ) -> dict[UUID, IngestionProgress]:
        if not document_ids:
            return {}
        try:
            values = await self._redis.mget([_key(tenant_id, d) for d in document_ids])
        except Exception:
            logger.warning("could not read ingestion progress for tenant %s", tenant_id)
            return {}
        found: dict[UUID, IngestionProgress] = {}
        for document_id, raw in zip(document_ids, values, strict=True):
            if raw is None:
                continue
            try:
                data = json.loads(raw)
                found[document_id] = IngestionProgress(
                    stage=IngestionStage(data["stage"]),
                    percent=int(data["percent"]),
                    detail=data.get("detail"),
                )
            except (ValueError, KeyError, TypeError):
                # A value this code did not write, or an older shape: skip it
                # rather than failing the whole list.
                continue
        return found
