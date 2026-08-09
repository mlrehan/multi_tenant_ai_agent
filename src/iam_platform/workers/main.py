"""Worker process entrypoint.

Run with::

    celery -A iam_platform.workers.main:celery_app worker \\
        --loglevel=INFO --queues=ingestion --concurrency=2

Concurrency is deliberately low by default: document parsing is CPU-bound
(docling reconstructs layout and may OCR), so oversubscribing cores makes
every job slower rather than more of them finish. Scale by adding worker
*processes*, not threads.
"""

from __future__ import annotations

import logging

from celery.signals import worker_shutdown

from iam_platform.core.config import Settings
from iam_platform.workers.celery_app import celery_app

# Importing the task module is what registers the tasks on `celery_app` --
# `autodiscover_tasks` finds `iam_platform.workers.jobs`, but the explicit
# import makes the dependency visible rather than relying on discovery order.
from iam_platform.workers.jobs import tasks as _tasks

__all__ = ["celery_app"]


# Untyped decorator from celery -- see the note in jobs/tasks.py.
@worker_shutdown.connect  # type: ignore[untyped-decorator]
def _on_worker_shutdown(**kwargs: object) -> None:
    _tasks.shutdown_worker(**kwargs)


def _configure_logging() -> None:
    settings = Settings()
    logging.basicConfig(
        level=settings.log_level,
        format='{"level": "%(levelname)s", "logger": "%(name)s", "message": "%(message)s"}',
    )


_configure_logging()
