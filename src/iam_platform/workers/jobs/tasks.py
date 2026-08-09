"""Celery task definitions -- the sync/async boundary.

Celery tasks are synchronous; this platform is async top to bottom. Each task
therefore owns an event loop and drives the async pipeline with
``loop.run_until_complete``.

**One loop per worker process, not per task.** ``asyncio.run`` would create and
tear down a loop on every job, and with it every connection pool the container
holds -- so a worker processing a hundred documents would open and close a
hundred Postgres pools. The container and its loop are built once, lazily, on
first task execution (not at import, which would run before Celery has forked
its worker processes and would share a loop across forks).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

from iam_platform.application.ai_resources.ports import CrawlLimits
from iam_platform.infrastructure.crawling.crawl4ai_crawler import Crawl4AiWebCrawler
from iam_platform.infrastructure.crawling.url_safety import UrlSafetyPolicy
from iam_platform.infrastructure.parsing.chunking import TokenAwareChunker
from iam_platform.infrastructure.parsing.dispatcher import ParserDispatcher
from iam_platform.workers.bootstrap import WorkerContainer, build_worker_container
from iam_platform.workers.celery_app import celery_app
from iam_platform.workers.jobs.process_document_upload import (
    IngestionDependencies,
    process_document_upload,
)
from iam_platform.workers.jobs.process_url_crawl import (
    CrawlDependencies,
    process_url_crawl,
)

logger = logging.getLogger("iam_platform.workers.jobs.tasks")

_loop: asyncio.AbstractEventLoop | None = None
_container: WorkerContainer | None = None


def _get_loop() -> asyncio.AbstractEventLoop:
    global _loop
    if _loop is None:
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
    return _loop


def _get_container() -> WorkerContainer:
    """Built once per worker process, on first use.

    Deliberately not at import time: Celery forks worker processes after
    importing task modules, and a connection pool created before the fork is
    inherited by every child -- which is a well-known way to get two processes
    using the same socket.
    """
    global _container
    if _container is None:
        _container = _get_loop().run_until_complete(build_worker_container())
    return _container


# `celery` ships no type information, so its decorator is untyped and mypy's
# strict mode flags the wrapped function as untyped too. Ignored narrowly here
# rather than relaxing the setting for the module -- everything else in this
# file is still fully checked.
@celery_app.task(  # type: ignore[untyped-decorator]
    name="ingestion.process_document_upload",
    # Retry transient faults (a Qdrant blip, an OpenAI 429) but not forever.
    # Note this is *separate* from marking the document failed: the job marks
    # the document failed on a genuine content problem and returns normally, so
    # a parse error does not consume retries.
    autoretry_for=(ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=300,
    max_retries=3,
)
def process_document_upload_task(
    tenant_id: str, actor_user_id: str, document_id: str
) -> None:
    """Payload is three ids as strings -- JSON-serializable, and small.

    Deliberately *not* the document's contents or any authorization decision:
    the job re-reads both from the database at execution time (see
    ``job_context``), so an enqueued payload conveys no privilege of its own.
    """
    container = _get_container()

    if container.embedding_client is None:
        # Raising (rather than marking the document failed) is right: nothing
        # is wrong with the document, the platform is misconfigured. The task
        # fails loudly and the document stays in `processing`, which is
        # accurate -- it genuinely is still awaiting ingestion.
        raise RuntimeError(
            "OPENAI__API_KEY is not configured -- cannot embed documents. "
            "The document remains queued for ingestion."
        )

    dependencies = IngestionDependencies(
        object_storage=container.object_storage,
        parser=ParserDispatcher(),
        chunker=TokenAwareChunker(container.settings.ingestion),
        embedding_client=container.embedding_client,
        vector_search=container.vector_search,
    )

    _get_loop().run_until_complete(
        process_document_upload(
            container.session_factory,
            dependencies,
            tenant_id=UUID(tenant_id),
            actor_user_id=UUID(actor_user_id),
            document_id=UUID(document_id),
        )
    )


@celery_app.task(  # type: ignore[untyped-decorator]
    name="ingestion.process_url_crawl",
    autoretry_for=(ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=300,
    # One retry, not three. A crawl can run for two hours and cost hundreds of
    # embedding calls; retrying a transient blip is worth it, retrying three
    # times is potentially six hours of work and three times the bill for a
    # site that may simply be down.
    max_retries=1,
)
def process_url_crawl_task(
    tenant_id: str, actor_user_id: str, data_source_id: str
) -> None:
    """Payload is three ids, same shape and same reasoning as the upload task:
    it conveys no privilege, it names whose authorization to re-derive."""
    container = _get_container()

    if container.embedding_client is None:
        raise RuntimeError(
            "OPENAI__API_KEY is not configured -- cannot embed crawled pages. "
            "The crawl remains queued."
        )

    settings = container.settings
    dependencies = CrawlDependencies(
        crawler=Crawl4AiWebCrawler(
            UrlSafetyPolicy(
                allow_private_network_targets=settings.crawl.allow_private_network_targets
            )
        ),
        object_storage=container.object_storage,
        chunker=TokenAwareChunker(settings.ingestion),
        embedding_client=container.embedding_client,
        vector_search=container.vector_search,
        limits=CrawlLimits(
            max_depth=settings.crawl.max_depth,
            max_pages=settings.crawl.max_pages,
            page_timeout_seconds=settings.crawl.page_timeout_seconds,
            job_timeout_seconds=settings.crawl.job_timeout_seconds,
            respect_robots_txt=settings.crawl.respect_robots_txt,
            max_page_bytes=settings.crawl.max_page_bytes,
        ),
    )

    _get_loop().run_until_complete(
        process_url_crawl(
            container.session_factory,
            dependencies,
            tenant_id=UUID(tenant_id),
            actor_user_id=UUID(actor_user_id),
            data_source_id=UUID(data_source_id),
        )
    )


def shutdown_worker(**_: Any) -> None:
    """Disposes the container's connection pool on worker shutdown.

    Registered against Celery's ``worker_shutdown`` signal in ``main.py``.
    Phase 9 found nothing in the API was ever disposed; worker processes
    restart far more often, so the same leak would be worse here.
    """
    global _container, _loop
    if _container is not None and _loop is not None:
        _loop.run_until_complete(_container.shutdown())
        _container = None
