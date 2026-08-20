# """Celery task definitions -- the sync/async boundary.

# Celery tasks are synchronous; this platform is async top to bottom. Each task
# therefore owns an event loop and drives the async pipeline with
# ``loop.run_until_complete``.

# **One loop per worker process, not per task.** ``asyncio.run`` would create and
# tear down a loop on every job, and with it every connection pool the container
# holds -- so a worker processing a hundred documents would open and close a
# hundred Postgres pools. The container and its loop are built once, lazily, on
# first task execution (not at import, which would run before Celery has forked
# its worker processes and would share a loop across forks).
# """

# from __future__ import annotations

# import asyncio
# import logging
# from typing import Any
# from uuid import UUID

# from iam_platform.application.ai_resources.ports import CrawlLimits
# from iam_platform.infrastructure.crawling.crawl4ai_crawler import Crawl4AiWebCrawler
# from iam_platform.infrastructure.crawling.url_safety import UrlSafetyPolicy
# from iam_platform.infrastructure.parsing.chunking import TokenAwareChunker
# from iam_platform.infrastructure.parsing.dispatcher import ParserDispatcher
# from iam_platform.workers.bootstrap import WorkerContainer, build_worker_container
# from iam_platform.workers.celery_app import celery_app
# from iam_platform.workers.jobs.process_document_upload import (
#     IngestionDependencies,
#     process_document_upload,
# )
# from iam_platform.workers.jobs.process_url_crawl import (
#     CrawlDependencies,
#     process_url_crawl,
# )

# logger = logging.getLogger("iam_platform.workers.jobs.tasks")

# _loop: asyncio.AbstractEventLoop | None = None
# _container: WorkerContainer | None = None


# def _get_loop() -> asyncio.AbstractEventLoop:
#     global _loop
#     if _loop is None:
#         _loop = asyncio.new_event_loop()
#         asyncio.set_event_loop(_loop)
#     return _loop


# def _get_container() -> WorkerContainer:
#     """Built once per worker process, on first use.

#     Deliberately not at import time: Celery forks worker processes after
#     importing task modules, and a connection pool created before the fork is
#     inherited by every child -- which is a well-known way to get two processes
#     using the same socket.
#     """
#     global _container
#     if _container is None:
#         _container = _get_loop().run_until_complete(build_worker_container())
#     return _container


# # `celery` ships no type information, so its decorator is untyped and mypy's
# # strict mode flags the wrapped function as untyped too. Ignored narrowly here
# # rather than relaxing the setting for the module -- everything else in this
# # file is still fully checked.
# @celery_app.task(  # type: ignore[untyped-decorator]
#     name="ingestion.process_document_upload",
#     # Retry transient faults (a Qdrant blip, an OpenAI 429) but not forever.
#     # Note this is *separate* from marking the document failed: the job marks
#     # the document failed on a genuine content problem and returns normally, so
#     # a parse error does not consume retries.
#     autoretry_for=(ConnectionError, TimeoutError),
#     retry_backoff=True,
#     retry_backoff_max=300,
#     max_retries=3,
# )
# def process_document_upload_task(
#     tenant_id: str, actor_user_id: str, document_id: str
# ) -> None:
#     """Payload is three ids as strings -- JSON-serializable, and small.

#     Deliberately *not* the document's contents or any authorization decision:
#     the job re-reads both from the database at execution time (see
#     ``job_context``), so an enqueued payload conveys no privilege of its own.
#     """
#     container = _get_container()

#     if container.embedding_client is None:
#         # Raising (rather than marking the document failed) is right: nothing
#         # is wrong with the document, the platform is misconfigured. The task
#         # fails loudly and the document stays in `processing`, which is
#         # accurate -- it genuinely is still awaiting ingestion.
#         raise RuntimeError(
#             "OPENAI__API_KEY is not configured -- cannot embed documents. "
#             "The document remains queued for ingestion."
#         )

#     dependencies = IngestionDependencies(
#         object_storage=container.object_storage,
#         parser=ParserDispatcher(),
#         chunker=TokenAwareChunker(container.settings.ingestion),
#         embedding_client=container.embedding_client,
#         vector_search=container.vector_search,
#     )

#     _get_loop().run_until_complete(
#         process_document_upload(
#             container.session_factory,
#             dependencies,
#             tenant_id=UUID(tenant_id),
#             actor_user_id=UUID(actor_user_id),
#             document_id=UUID(document_id),
#         )
#     )


# @celery_app.task(  # type: ignore[untyped-decorator]
#     name="ingestion.process_url_crawl",
#     autoretry_for=(ConnectionError, TimeoutError),
#     retry_backoff=True,
#     retry_backoff_max=300,
#     # One retry, not three. A crawl can run for two hours and cost hundreds of
#     # embedding calls; retrying a transient blip is worth it, retrying three
#     # times is potentially six hours of work and three times the bill for a
#     # site that may simply be down.
#     max_retries=1,
# )
# def process_url_crawl_task(
#     tenant_id: str, actor_user_id: str, data_source_id: str
# ) -> None:
#     """Payload is three ids, same shape and same reasoning as the upload task:
#     it conveys no privilege, it names whose authorization to re-derive."""
#     container = _get_container()

#     if container.embedding_client is None:
#         raise RuntimeError(
#             "OPENAI__API_KEY is not configured -- cannot embed crawled pages. "
#             "The crawl remains queued."
#         )

#     settings = container.settings
#     dependencies = CrawlDependencies(
#         crawler=Crawl4AiWebCrawler(
#             UrlSafetyPolicy(
#                 allow_private_network_targets=settings.crawl.allow_private_network_targets
#             )
#         ),
#         object_storage=container.object_storage,
#         chunker=TokenAwareChunker(settings.ingestion),
#         embedding_client=container.embedding_client,
#         vector_search=container.vector_search,
#         limits=CrawlLimits(
#             max_depth=settings.crawl.max_depth,
#             max_pages=settings.crawl.max_pages,
#             page_timeout_seconds=settings.crawl.page_timeout_seconds,
#             job_timeout_seconds=settings.crawl.job_timeout_seconds,
#             respect_robots_txt=settings.crawl.respect_robots_txt,
#             max_page_bytes=settings.crawl.max_page_bytes,
#         ),
#     )

#     _get_loop().run_until_complete(
#         process_url_crawl(
#             container.session_factory,
#             dependencies,
#             tenant_id=UUID(tenant_id),
#             actor_user_id=UUID(actor_user_id),
#             data_source_id=UUID(data_source_id),
#         )
#     )


# def shutdown_worker(**_: Any) -> None:
#     """Disposes the container's connection pool on worker shutdown.

#     Registered against Celery's ``worker_shutdown`` signal in ``main.py``.
#     Phase 9 found nothing in the API was ever disposed; worker processes
#     restart far more often, so the same leak would be worse here.
#     """
#     global _container, _loop
#     if _container is not None and _loop is not None:
#         _loop.run_until_complete(_container.shutdown())
#         _container = None






"""Celery task definitions -- the synchronous/asyncio boundary.

Celery task functions are synchronous, while this platform's ingestion
pipeline is asynchronous.

A threaded Celery worker may execute several synchronous task functions
concurrently in different OS threads. Those threads must not drive the same
asyncio event loop with ``run_until_complete()``.

Instead, this module owns one dedicated asyncio runtime thread per worker
process. Celery task threads submit coroutines to that event loop using
``asyncio.run_coroutine_threadsafe()`` and synchronously wait for the returned
``concurrent.futures.Future``.

Benefits:

* One long-lived asyncio event loop per worker process.
* One long-lived WorkerContainer per worker process.
* One SQLAlchemy async engine/pool per worker process.
* Safe operation with Celery's threads pool.
* Lazy initialization after Celery has started the worker process.
* Deterministic async cleanup during worker shutdown.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar
from uuid import UUID

from iam_platform.application.ai_resources.ports import CrawlLimits
from iam_platform.infrastructure.crawling.crawl4ai_crawler import (
    Crawl4AiWebCrawler,
)
from iam_platform.infrastructure.crawling.url_safety import UrlSafetyPolicy
from iam_platform.infrastructure.parsing.chunking import TokenAwareChunker
from iam_platform.infrastructure.parsing.dispatcher import ParserDispatcher
from iam_platform.workers.bootstrap import (
    WorkerContainer,
    build_worker_container,
)
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

_T = TypeVar("_T")


class _AsyncWorkerRuntime:
    """Own a single asyncio event loop running in a dedicated thread.

    Celery task threads never execute ``run_until_complete()`` and never
    directly drive the asyncio loop.

    They submit coroutine work via ``asyncio.run_coroutine_threadsafe()``.

    The runtime is created lazily so importing this module does not create an
    event loop, thread, database connection pool, or other async resource
    before Celery has established the worker process.
    """

    def __init__(self) -> None:
        self._state_lock = threading.RLock()
        self._drained = threading.Condition(self._state_lock)

        self._ready = threading.Event()

        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None

        self._startup_error: BaseException | None = None

        # Number of synchronous Celery calls currently waiting for async work.
        self._active_calls = 0

        # Prevent new submissions while shutdown is draining existing calls.
        self._stopping = False

    async def _serve(self) -> None:
        """Keep the dedicated asyncio event loop alive until shutdown."""
        loop = asyncio.get_running_loop()
        stop_event = asyncio.Event()

        with self._state_lock:
            self._loop = loop
            self._stop_event = stop_event

        # start() may now safely allow task threads to submit work.
        self._ready.set()

        await stop_event.wait()

    def _thread_main(self) -> None:
        """Entry point for the dedicated asyncio runtime thread."""
        try:
            # asyncio.run() owns creation and final cleanup of this loop,
            # including async-generator and default-executor cleanup.
            asyncio.run(self._serve())

        except BaseException as exc:
            with self._state_lock:
                self._startup_error = exc

            logger.exception(
                "worker asyncio runtime thread terminated unexpectedly"
            )

        finally:
            # Prevent start() from waiting forever if startup failed before
            # _serve() was able to signal readiness.
            self._ready.set()

    def start(self) -> None:
        """Start the runtime thread if it has not already been started."""
        with self._state_lock:
            if self._stopping:
                raise RuntimeError(
                    "worker asyncio runtime is shutting down"
                )

            thread = self._thread

            if (
                thread is not None
                and thread.is_alive()
                and self._loop is not None
                and self._stop_event is not None
            ):
                return

            if thread is None or not thread.is_alive():
                self._ready = threading.Event()
                self._startup_error = None
                self._loop = None
                self._stop_event = None

                thread = threading.Thread(
                    target=self._thread_main,
                    name="iam-platform-asyncio-worker",
                    daemon=True,
                )

                self._thread = thread
                thread.start()

            ready = self._ready

        # Important:
        # Do not hold _state_lock while waiting here because _serve() needs
        # that lock to publish _loop and _stop_event.
        ready.wait()

        with self._state_lock:
            if self._startup_error is not None:
                raise RuntimeError(
                    "failed to start worker asyncio runtime"
                ) from self._startup_error

            thread = self._thread

            if (
                thread is None
                or not thread.is_alive()
                or self._loop is None
                or self._stop_event is None
            ):
                raise RuntimeError(
                    "worker asyncio runtime failed to initialize"
                )

    def run(
        self,
        coroutine_factory: Callable[
            [],
            Coroutine[Any, Any, _T],
        ],
    ) -> _T:
        """Execute one coroutine on the dedicated asyncio loop.

        This method is called by ordinary Celery task threads.

        The coroutine is intentionally created only after the runtime has
        started and accepted this call. That prevents an un-awaited coroutine
        warning if shutdown has already begun.
        """
        self.start()

        with self._state_lock:
            if self._stopping:
                raise RuntimeError(
                    "worker asyncio runtime is shutting down"
                )

            loop = self._loop

            if loop is None or not loop.is_running():
                raise RuntimeError(
                    "worker asyncio runtime loop is not running"
                )

            self._active_calls += 1

        coroutine: Coroutine[Any, Any, _T] | None = None

        try:
            coroutine = coroutine_factory()

            try:
                future = asyncio.run_coroutine_threadsafe(
                    coroutine,
                    loop,
                )

            except BaseException:
                # Submission failed before ownership of the coroutine moved
                # to the event loop. Close it explicitly to avoid:
                #
                # RuntimeWarning: coroutine '...' was never awaited
                coroutine.close()
                raise

            # concurrent.futures.Future.result() blocks this Celery task
            # thread while the asynchronous operation itself executes on
            # the dedicated asyncio thread.
            #
            # Any exception raised by the coroutine is re-raised here and
            # therefore remains visible to Celery's autoretry machinery.
            return future.result()

        finally:
            with self._state_lock:
                self._active_calls -= 1

                if self._active_calls == 0:
                    self._drained.notify_all()

    def stop(
        self,
        shutdown_factory: Callable[
            [],
            Coroutine[Any, Any, None],
        ]
        | None = None,
    ) -> None:
        """Drain work, clean up async resources, and stop the runtime."""
        with self._state_lock:
            thread = self._thread

            if thread is None:
                return

            if self._stopping:
                other_stop_in_progress = True

            else:
                other_stop_in_progress = False
                self._stopping = True

                # Normally Celery calls worker_shutdown after the worker pool
                # has drained. This also protects us if shutdown arrives while
                # task threads are still waiting for asynchronous operations.
                while self._active_calls > 0:
                    self._drained.wait()

                loop = self._loop
                stop_event = self._stop_event

        # If another thread is already performing shutdown, simply wait for
        # that shutdown to complete.
        if other_stop_in_progress:
            if thread is not threading.current_thread():
                thread.join()

            return

        shutdown_error: BaseException | None = None

        # Dispose async resources while their owning event loop is still alive.
        if (
            loop is not None
            and loop.is_running()
            and shutdown_factory is not None
        ):
            shutdown_coro = shutdown_factory()
            submitted = False

            try:
                future = asyncio.run_coroutine_threadsafe(
                    shutdown_coro,
                    loop,
                )
                submitted = True
                future.result()

            except BaseException as exc:
                shutdown_error = exc

                # If submission itself failed, asyncio never took ownership.
                if not submitted:
                    shutdown_coro.close()

        # Ask _serve() to return. asyncio.run() then performs normal loop
        # cleanup before _thread_main exits.
        if (
            loop is not None
            and loop.is_running()
            and stop_event is not None
        ):
            loop.call_soon_threadsafe(stop_event.set)

        if thread is not threading.current_thread():
            thread.join()

        with self._state_lock:
            self._thread = None
            self._loop = None
            self._stop_event = None
            self._startup_error = None
            self._active_calls = 0
            self._stopping = False

        if shutdown_error is not None:
            raise shutdown_error


# ---------------------------------------------------------------------------
# Process-local async runtime
# ---------------------------------------------------------------------------

_runtime = _AsyncWorkerRuntime()


# ---------------------------------------------------------------------------
# Worker container
#
# These globals are created and accessed ONLY from _runtime's asyncio thread.
# ---------------------------------------------------------------------------

_container: WorkerContainer | None = None
_container_lock: asyncio.Lock | None = None


async def _get_container_async() -> WorkerContainer:
    """Build and return the worker container exactly once.

    Multiple Celery tasks may arrive at the same time. Their corresponding
    coroutines therefore may concurrently reach this function.

    The asyncio lock prevents duplicate construction of SQLAlchemy engines,
    Qdrant clients, embedding clients, and object-storage clients.
    """
    global _container, _container_lock

    if _container is not None:
        return _container

    if _container_lock is None:
        # Created inside the dedicated asyncio loop, never in a Celery thread.
        _container_lock = asyncio.Lock()

    async with _container_lock:
        # Double-check after acquiring the lock because another coroutine may
        # have completed initialization while this one was waiting.
        if _container is None:
            logger.info("initializing worker async container")

            _container = await build_worker_container()

            logger.info("worker async container initialized")

        return _container


async def _shutdown_container_async() -> None:
    """Dispose the worker container before the event loop terminates."""
    global _container, _container_lock

    container = _container

    # Remove the global reference first so it can never accidentally be reused
    # after shutdown begins.
    _container = None

    if container is not None:
        logger.info("shutting down worker async container")

        await container.shutdown()

        logger.info("worker async container shut down")

    _container_lock = None


# ---------------------------------------------------------------------------
# Async task implementations
#
# Everything that constructs or uses async infrastructure lives here so it
# executes on the dedicated event-loop thread.
# ---------------------------------------------------------------------------


async def _process_document_upload_async(
    tenant_id: str,
    actor_user_id: str,
    document_id: str,
) -> None:
    """Async implementation of document ingestion."""
    container = await _get_container_async()

    if container.embedding_client is None:
        # This is infrastructure/platform misconfiguration rather than invalid
        # document content. Do not mark the document itself as bad content.
        raise RuntimeError(
            "OPENAI__API_KEY is not configured -- cannot embed documents. "
            "The document remains queued for ingestion."
        )

    dependencies = IngestionDependencies(
        object_storage=container.object_storage,
        parser=ParserDispatcher(),
        chunker=TokenAwareChunker(
            container.settings.ingestion
        ),
        embedding_client=container.embedding_client,
        vector_search=container.vector_search,
    )

    await process_document_upload(
        container.session_factory,
        dependencies,
        tenant_id=UUID(tenant_id),
        actor_user_id=UUID(actor_user_id),
        document_id=UUID(document_id),
    )


async def _process_url_crawl_async(
    tenant_id: str,
    actor_user_id: str,
    data_source_id: str,
) -> None:
    """Async implementation of URL/site crawling."""
    container = await _get_container_async()

    if container.embedding_client is None:
        raise RuntimeError(
            "OPENAI__API_KEY is not configured -- cannot embed crawled pages. "
            "The crawl remains queued."
        )

    settings = container.settings

    dependencies = CrawlDependencies(
        crawler=Crawl4AiWebCrawler(
            UrlSafetyPolicy(
                allow_private_network_targets=(
                    settings.crawl.allow_private_network_targets
                )
            )
        ),
        object_storage=container.object_storage,
        chunker=TokenAwareChunker(
            settings.ingestion
        ),
        embedding_client=container.embedding_client,
        vector_search=container.vector_search,
        limits=CrawlLimits(
            max_depth=settings.crawl.max_depth,
            max_pages=settings.crawl.max_pages,
            page_timeout_seconds=(
                settings.crawl.page_timeout_seconds
            ),
            job_timeout_seconds=(
                settings.crawl.job_timeout_seconds
            ),
            respect_robots_txt=(
                settings.crawl.respect_robots_txt
            ),
            max_page_bytes=settings.crawl.max_page_bytes,
        ),
    )

    await process_url_crawl(
        container.session_factory,
        dependencies,
        tenant_id=UUID(tenant_id),
        actor_user_id=UUID(actor_user_id),
        data_source_id=UUID(data_source_id),
    )


# ---------------------------------------------------------------------------
# Celery synchronous task entry points
# ---------------------------------------------------------------------------


@celery_app.task(  # type: ignore[untyped-decorator]
    name="ingestion.process_document_upload",
    autoretry_for=(
        ConnectionError,
        TimeoutError,
    ),
    retry_backoff=True,
    retry_backoff_max=300,
    max_retries=3,
)
def process_document_upload_task(
    tenant_id: str,
    actor_user_id: str,
    document_id: str,
) -> None:
    """Process one uploaded document.

    The Celery task itself remains synchronous. The asynchronous implementation
    runs entirely on the dedicated worker asyncio event loop.
    """
    _runtime.run(
        lambda: _process_document_upload_async(
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            document_id=document_id,
        )
    )


@celery_app.task(  # type: ignore[untyped-decorator]
    name="ingestion.process_url_crawl",
    autoretry_for=(
        ConnectionError,
        TimeoutError,
    ),
    retry_backoff=True,
    retry_backoff_max=300,
    max_retries=1,
)
def process_url_crawl_task(
    tenant_id: str,
    actor_user_id: str,
    data_source_id: str,
) -> None:
    """Process one URL/site crawl."""
    _runtime.run(
        lambda: _process_url_crawl_async(
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            data_source_id=data_source_id,
        )
    )


# ---------------------------------------------------------------------------
# Worker lifecycle
# ---------------------------------------------------------------------------


def shutdown_worker(**_: Any) -> None:
    """Dispose async resources and terminate the worker asyncio runtime.

    This function can continue to be registered with the Celery
    ``worker_shutdown`` signal as in the current application.
    """
    try:
        _runtime.stop(
            _shutdown_container_async
        )

    except BaseException:
        # A cleanup failure should be visible operationally, but must not
        # prevent the Celery worker process from continuing its shutdown.
        logger.exception(
            "failed to shut down worker asyncio runtime cleanly"
        )