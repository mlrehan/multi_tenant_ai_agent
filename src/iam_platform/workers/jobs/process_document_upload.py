"""The ingestion pipeline: bytes in object storage → searchable vectors.

Phase 7 created ``documents`` rows with ``status='processing'`` and nothing
that ever moved them. This closes that loop.

**Every exit path is terminal.** A document must never be left in
``processing`` -- that state means "a worker is coming", and a document stuck
there is indistinguishable from one still in the queue. So the pipeline runs
inside a try/except whose handler marks the row ``failed`` with a readable
reason, and the only way to reach ``ready`` is to have finished.

**The status write must not be rolled back by the failure that caused it.**
That is the Phase 5 Unit-of-Work pitfall recorded in docs/18: writing a
side effect and then raising inside the same ``async with`` block rolls the
write back, because ``__aexit__`` rolls back on any exception. So failures are
recorded in their *own* transaction, opened after the failing one has already
unwound -- "exit the block normally, record after".

**Idempotent by construction.** A redelivered job (Celery's ``acks_late``
returns work from a killed worker to the queue) re-parses, deletes the
document's existing chunk rows and vectors, and rewrites them. Running twice
produces the same state as running once, which is what makes at-least-once
delivery safe.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from iam_platform.application.ai_resources.exceptions import (
    DocumentContentNotFoundError,
    DocumentParseError,
    UnsupportedDocumentTypeError,
)
from iam_platform.application.ai_resources.ports import (
    DocumentParser,
    EmbeddingClient,
    ObjectStorageClient,
    VectorSearchClient,
)
from iam_platform.infrastructure.parsing.chunking import TokenAwareChunker
from iam_platform.workers.job_context import (
    JobAuthorizationError,
    VerifiedJobContext,
    establish_job_context,
)
from iam_platform.workers.jobs.indexing import IndexingTarget, index_blocks

logger = logging.getLogger("iam_platform.workers.jobs.process_document_upload")


@dataclass(frozen=True, slots=True)
class _DocumentRow:
    knowledge_base_id: UUID
    filename: str
    content_type: str
    storage_path: str
    vector_namespace: str


@dataclass(frozen=True, slots=True)
class IngestionDependencies:
    """Everything the job needs, injected rather than constructed.

    Bundled into one object because the job is called from a Celery task that
    has no DI container -- passing eight arguments through that boundary is
    how they end up in the wrong order.
    """

    object_storage: ObjectStorageClient
    parser: DocumentParser
    chunker: TokenAwareChunker
    embedding_client: EmbeddingClient
    vector_search: VectorSearchClient


class DocumentIngestionFailed(Exception):
    """Wraps whatever went wrong with a message fit to show a tenant."""


async def process_document_upload(
    session_factory: Callable[[], AsyncSession],
    dependencies: IngestionDependencies,
    *,
    tenant_id: UUID,
    actor_user_id: UUID,
    document_id: UUID,
) -> None:
    """Entry point. Never raises for an ingestion failure -- it records one.

    Re-raises only ``JobAuthorizationError``, because that is not a document
    problem: the job should be dropped, and marking the document ``failed``
    would be wrong (nothing is wrong with it) *and* impossible (the RLS context
    that would let us write the row is exactly what was refused).
    """
    try:
        async with session_factory() as session, session.begin():
            context = await establish_job_context(
                session, tenant_id=tenant_id, actor_user_id=actor_user_id
            )
            document = await _load_document(session, document_id=document_id)
            await _ingest(
                session,
                dependencies,
                context=context,
                document_id=document_id,
                document=document,
            )
            await _mark_ready(session, document_id=document_id)
        logger.info(
            "ingested document %s for tenant %s", document_id, tenant_id, extra={"ok": True}
        )
    except JobAuthorizationError:
        # Deliberately not recorded on the document: the authorization to
        # write that row is precisely what was just refused.
        logger.warning(
            "refusing document %s: job authorization no longer valid", document_id
        )
        raise
    except Exception as exc:
        reason = _readable_reason(exc)
        logger.exception("ingestion failed for document %s: %s", document_id, reason)
        # A *separate* transaction, opened only after the failing one has
        # unwound -- see the module docstring. Writing this inside the block
        # above would have it rolled back by the very exception it records.
        await _mark_failed(
            session_factory,
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            document_id=document_id,
            reason=reason,
        )


def _readable_reason(exc: Exception) -> str:
    """Turns an exception into something a tenant can act on.

    Known failure types keep their message (they were written for this).
    Anything else is deliberately generic: an unexpected traceback can carry
    internal paths, table names, or credentials, and the document owner is not
    the right audience for those -- the full detail goes to the log instead.
    """
    if isinstance(exc, DocumentParseError | UnsupportedDocumentTypeError):
        return str(exc)
    if isinstance(exc, DocumentContentNotFoundError):
        return "the uploaded file could not be found in storage"
    return "an unexpected error occurred while processing this document"


async def _load_document(session: AsyncSession, *, document_id: UUID) -> _DocumentRow:
    row = (
        await session.execute(
            text(
                "SELECT d.knowledge_base_id, d.filename, d.content_type, d.storage_path, "
                "       kb.vector_namespace "
                "FROM documents d "
                "JOIN knowledge_bases kb ON kb.id = d.knowledge_base_id "
                "WHERE d.id = :did AND d.deleted_at IS NULL"
            ),
            {"did": str(document_id)},
        )
    ).first()
    if row is None:
        # RLS-scoped, so this also covers "belongs to another tenant" -- the
        # job context was set from the claimed tenant, so a document outside it
        # is simply not visible.
        raise DocumentIngestionFailed(
            f"document {document_id} not found, deleted, or not in this tenant"
        )
    return _DocumentRow(
        knowledge_base_id=row[0],
        filename=row[1],
        content_type=row[2],
        storage_path=row[3],
        vector_namespace=row[4],
    )


async def _ingest(
    session: AsyncSession,
    dependencies: IngestionDependencies,
    *,
    context: VerifiedJobContext,
    document_id: UUID,
    document: _DocumentRow,
) -> None:
    """Fetch, parse, then hand off to the shared indexing step.

    Only the first two lines are specific to an *uploaded* document. Everything
    after them is identical to what a crawled page needs, and lives in
    `indexing.py` so there is one copy of it rather than two.
    """
    data = await dependencies.object_storage.get(path=document.storage_path)

    blocks = await dependencies.parser.parse(
        data=data, content_type=document.content_type, filename=document.filename
    )

    await index_blocks(
        session,
        target=IndexingTarget(
            tenant_id=context.tenant_id,
            knowledge_base_id=document.knowledge_base_id,
            document_id=document_id,
            vector_namespace=document.vector_namespace,
        ),
        blocks=blocks,
        chunker=dependencies.chunker,
        embedding_client=dependencies.embedding_client,
        vector_search=dependencies.vector_search,
    )


async def _mark_ready(session: AsyncSession, *, document_id: UUID) -> None:
    await session.execute(
        text(
            "UPDATE documents SET status = 'ready', failure_reason = NULL "
            "WHERE id = :did"
        ),
        {"did": str(document_id)},
    )


async def _mark_failed(
    session_factory: Callable[[], AsyncSession],
    *,
    tenant_id: UUID,
    actor_user_id: UUID,
    document_id: UUID,
    reason: str,
) -> None:
    """Records the failure in its own transaction.

    Best-effort: if this itself fails there is nothing further to do but log,
    and raising here would replace a specific ingestion error with a generic
    database one in the worker's logs.
    """
    try:
        async with session_factory() as session, session.begin():
            # RLS context is needed again -- this is a new transaction, and
            # `set_config(..., true)` did not survive the last one.
            await session.execute(
                text("SELECT set_config('app.user_id', :uid, true)"),
                {"uid": str(actor_user_id)},
            )
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tid, true)"),
                {"tid": str(tenant_id)},
            )
            await session.execute(
                text(
                    "UPDATE documents SET status = 'failed', failure_reason = :reason "
                    "WHERE id = :did"
                ),
                {"did": str(document_id), "reason": reason},
            )
    except Exception:
        logger.exception(
            "could not record ingestion failure for document %s -- it will remain "
            "in 'processing' and needs manual attention",
            document_id,
        )
