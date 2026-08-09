"""Celery-backed ``DocumentIngestionQueue`` -- replaces the logging stand-in.

Sends by task *name* rather than importing the task function. Importing it
would pull ``workers.jobs.tasks`` into the API process, which in turn builds a
worker container and its connection pools -- an entire second stack of
resources in a process that will never execute a job. The name is the contract
between the two processes, exactly as it is for any other queue.

``apply_async`` is a synchronous network call to Redis, run on a thread so it
cannot block the event loop mid-request. It is fast, but "fast" is not "never
slow": a Redis failover would otherwise stall every concurrent request on the
worker's event loop rather than just this one.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from iam_platform.core.config import Settings

logger = logging.getLogger("iam_platform.infrastructure.queue.celery")

#: Must match ``@celery_app.task(name=...)`` in workers/jobs/tasks.py. A
#: mismatch means jobs are enqueued to a name nothing consumes -- documents
#: would sit in `processing` forever with no error anywhere, so this constant
#: is asserted against the task's registered name in the test suite.
INGESTION_TASK_NAME = "ingestion.process_document_upload"
INGESTION_QUEUE = "ingestion"


class CeleryDocumentIngestionQueue:
    def __init__(self, settings: Settings, *, celery_app: Any | None = None) -> None:
        if celery_app is not None:
            # Injectable so tests can assert on the enqueued payload
            # without a broker.
            self._app: Any = celery_app
            return

        from celery import Celery

        # A *send-only* client: no task registry, no worker config. The API
        # only ever produces, and giving it a fully-configured worker app would
        # invite someone to execute jobs in the web process by accident.
        self._app = Celery(broker=settings.redis.url.get_secret_value())

    async def enqueue(
        self, *, tenant_id: UUID, actor_user_id: UUID, document_id: UUID, at: datetime
    ) -> None:
        # `at` is accepted for the port's shape (a future scheduled ingestion)
        # but not used: nothing schedules a delayed ingestion today, and
        # passing an `eta` no caller sets would be behaviour nobody asked for
        # and nothing tests.
        del at

        await asyncio.to_thread(
            self._app.send_task,
            INGESTION_TASK_NAME,
            kwargs={
                "tenant_id": str(tenant_id),
                "actor_user_id": str(actor_user_id),
                "document_id": str(document_id),
            },
            queue=INGESTION_QUEUE,
        )
        logger.info("enqueued ingestion for document %s (tenant %s)", document_id, tenant_id)


#: Must match ``@celery_app.task(name=...)`` in workers/jobs/tasks.py, for the
#: same reason as the ingestion name above: a mismatch enqueues to a name
#: nothing consumes, and the source sits in `syncing` forever with no error
#: anywhere. Asserted against the registered task name in the test suite.
CRAWL_TASK_NAME = "ingestion.process_url_crawl"


class CeleryCrawlJobQueue:
    """Celery-backed ``CrawlJobQueue``.

    Deliberately a second class rather than a method on the ingestion queue:
    the two carry different payloads and, when crawl volume justifies its own
    worker pool, will carry different queue names too. One class doing both
    would have to grow a mode flag to be split later.
    """

    def __init__(self, settings: Settings, *, celery_app: Any | None = None) -> None:
        if celery_app is not None:
            self._app: Any = celery_app
            return

        from celery import Celery

        self._app = Celery(broker=settings.redis.url.get_secret_value())

    async def enqueue(
        self, *, tenant_id: UUID, actor_user_id: UUID, data_source_id: UUID, at: datetime
    ) -> None:
        # Accepted for the port's shape (a future scheduled re-crawl -- the
        # obvious next feature) but unused today, matching the ingestion queue.
        del at

        await asyncio.to_thread(
            self._app.send_task,
            CRAWL_TASK_NAME,
            kwargs={
                "tenant_id": str(tenant_id),
                "actor_user_id": str(actor_user_id),
                "data_source_id": str(data_source_id),
            },
            queue=INGESTION_QUEUE,
        )
        logger.info(
            "enqueued crawl for data source %s (tenant %s)", data_source_id, tenant_id
        )
