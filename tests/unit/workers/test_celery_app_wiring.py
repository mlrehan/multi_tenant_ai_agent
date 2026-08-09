"""Guards the worker's *entrypoint*, not its logic.

Every other test in this suite imports `process_document_upload` directly and
drives it with fakes. That proves the pipeline is correct and proves nothing
about whether `celery -A iam_platform.workers.main:celery_app worker` can even
start -- which is the only way the pipeline ever actually runs.

It could not. `build_celery_app()` called `autodiscover_tasks(..., force=True)`,
so discovery ran while the module defining `celery_app` was still executing;
discovery imported `workers.jobs.tasks`, which imports `celery_app` back out of
that half-initialized module, and the worker died on an ImportError before
accepting a single job. The whole of Phase 11 was inert and every test passed.

These tests exercise the import path Celery itself uses.
"""

from __future__ import annotations

from iam_platform.workers.celery_app import INGESTION_QUEUE


def test_worker_entrypoint_imports_and_registers_the_ingestion_task() -> None:
    """Imports `workers.main` the way the `celery -A` CLI does.

    A plain import is the entire assertion for the circular-import regression:
    before the fix this line raised ImportError.
    """
    from iam_platform.workers.main import celery_app

    assert "ingestion.process_document_upload" in celery_app.tasks


def test_ingestion_task_is_configured_for_at_least_once_delivery() -> None:
    """`acks_late` is what makes a worker killed mid-parse re-run its job
    instead of losing it silently, leaving the document in `processing`
    forever. It is only safe because the job is idempotent (proven in
    tests/unit/ai_resources/test_ingestion_job.py) -- so the two belong
    together, and neither should be changed without the other."""
    from iam_platform.workers.main import celery_app

    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.task_reject_on_worker_lost is True
    # Prefetching would let one worker reserve several long parses while
    # another sits idle.
    assert celery_app.conf.worker_prefetch_multiplier == 1
    assert celery_app.conf.task_default_queue == INGESTION_QUEUE
