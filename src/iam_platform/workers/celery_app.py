"""Celery application -- the worker process's task registry and broker config.

Redis is already a dependency of this platform (cache, rate limiting, MFA
challenge store), so it serves as the broker rather than introducing RabbitMQ
for one queue. Note the deliberate asymmetry with how Redis is used elsewhere:
the cache is explicitly *never authoritative* and fails closed
(docs/06-authorization-model.md), whereas the broker genuinely does hold the
only copy of a pending job. That is acceptable here because a lost ingestion
job is recoverable -- the document row survives in Postgres with
``status='processing'``, so a re-enqueue re-runs it -- but it is a real
difference in trust, and anything less recoverable would need a durable broker.

**Why `acks_late` and `reject_on_worker_lost`:** ingestion is long-running
(parsing a large PDF, embedding hundreds of chunks). The default acknowledges
a task on *receipt*, so a worker killed mid-parse loses the job silently and
the document stays in ``processing`` forever. Acknowledging on completion
instead means a killed worker's job returns to the queue.

**Idempotency is what makes redelivery safe.** A redelivered ingestion job
re-parses and re-embeds, then upserts by deterministic chunk id and replaces
the document's chunk rows -- so the outcome is the same whether it runs once
or three times. That property is a requirement of the job, not of Celery.
"""

from __future__ import annotations

from celery import Celery

from iam_platform.core.config import Settings

#: Single queue for now. Split by task type when ingestion volume justifies
#: separate worker pools -- a crawl job and a document parse have very
#: different runtimes, and one starving the other is a real failure mode.
INGESTION_QUEUE = "ingestion"


def build_celery_app(settings: Settings | None = None) -> Celery:
    resolved = settings or Settings()
    broker_url = resolved.redis.url.get_secret_value()

    app = Celery("iam_platform", broker=broker_url, backend=None)
    app.conf.update(
        task_default_queue=INGESTION_QUEUE,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        # No result backend: nothing polls task results. Ingestion progress is
        # read from `documents.status` in Postgres, which is the record of
        # truth and survives a broker flush. A result backend would be a
        # second, weaker source of the same answer.
        task_ignore_result=True,
        task_serializer="json",
        accept_content=["json"],
        # UTC everywhere, matching the rest of the platform's timestamps.
        enable_utc=True,
        timezone="UTC",
        # Fetch one task at a time. The default prefetch of 4 would let one
        # worker reserve several long parses while another sits idle.
        worker_prefetch_multiplier=1,
    )
    # `force=True` would discover tasks *here*, during `build_celery_app()` --
    # which is called at module level to create `celery_app` below. Discovery
    # imports `workers.jobs.tasks`, which does `from ...celery_app import
    # celery_app`, and that name does not exist yet: the module is still
    # executing. The result is an ImportError that no test catches, because
    # tests import the job function directly and never go through Celery's
    # discovery path. Left lazy, Celery defers discovery to worker startup,
    # by which time this module is fully initialized.
    app.autodiscover_tasks(["iam_platform.workers.jobs"])
    return app


#: Module-level instance for the `celery -A` CLI to find.
celery_app = build_celery_app()
