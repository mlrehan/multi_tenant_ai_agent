"""Test stand-in for ``DocumentIngestionQueue`` -- logs instead of enqueueing.

Superseded in real use by ``CeleryDocumentIngestionQueue`` (Phase 11). Kept
because the API test suite has no broker and no worker, and a test that
uploads a document should not need either to assert on the upload itself.

**Not a production fallback.** ``bootstrap`` always wires the Celery queue;
substituting this one would silently drop every ingestion job while reporting
the upload succeeded, leaving documents in ``processing`` forever with nothing
in the logs to explain it -- the same inert-failure shape as the vector-search
stand-in (see ``infrastructure/vector/unconfigured.py``).
"""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

logger = logging.getLogger("iam_platform.queue")


class LoggingDocumentIngestionQueue:
    async def enqueue(
        self, *, tenant_id: UUID, actor_user_id: UUID, document_id: UUID, at: datetime
    ) -> None:
        logger.info(
            "document ingestion queued",
            extra={
                "extra_fields": {
                    "tenant_id": str(tenant_id),
                    "actor_user_id": str(actor_user_id),
                    "document_id": str(document_id),
                }
            },
        )


class LoggingCrawlJobQueue:
    """Test/unconfigured stand-in for ``CrawlJobQueue``."""

    def __init__(self) -> None:
        self.enqueued: list[tuple[UUID, UUID, UUID]] = []

    async def enqueue(
        self, *, tenant_id: UUID, actor_user_id: UUID, data_source_id: UUID, at: datetime
    ) -> None:
        del at
        self.enqueued.append((tenant_id, actor_user_id, data_source_id))
        logger.info(
            "would enqueue crawl for data source %s (tenant %s)",
            data_source_id,
            tenant_id,
        )
