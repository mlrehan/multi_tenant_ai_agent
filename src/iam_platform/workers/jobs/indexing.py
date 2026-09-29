"""The one place text becomes searchable vectors.

Extracted from ``process_document_upload`` when Phase 12 added crawling,
because both jobs need exactly this and duplicating it would mean two copies of
the parts that are easy to get subtly wrong: the delete-before-write ordering
that makes redelivery safe, the ``strict=True`` zip that stops a chunk being
indexed under another chunk's vector, and the chunk rows that let Qdrant be
rebuilt without re-parsing (or re-paying for) the source.

The two callers differ only in where the text came from — an uploaded file that
had to be fetched and parsed, or a crawled page that arrived as markdown. By
the time either reaches here the difference is gone: both hold
``ParsedBlock``s, and everything downstream is identical. That is the seam
docs/24 asked for when it said crawled pages feed "the **same**
chunking/embedding/Qdrant-upsert pipeline — no separate code path".
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from iam_platform.application.ai_resources.ports import (
    INGESTION_CHANNEL,
    EmbeddingClient,
    IngestionStage,
    ParsedBlock,
    TokenUsage,
    UsageLedger,
    VectorChunk,
    VectorSearchClient,
)
from iam_platform.application.ai_resources.usage_recording import record_embedding_usage
from iam_platform.infrastructure.parsing.chunking import TokenAwareChunker

logger = logging.getLogger("iam_platform.workers.jobs.indexing")

#: Called as each stage starts: (stage, overall percent 0-100, detail).
ProgressCallback = Callable[[IngestionStage, int, "str | None"], Awaitable[None]]

#: Where each stage sits on one 0-100 bar per file. Extraction is the upload
#: job's; the rest happen here. Embedding gets the widest band because it is
#: the only stage whose length grows with the document *and* is reported in
#: steps (`EMBED_SLICE`).
PERCENT_EXTRACTING = 5
PERCENT_CHUNKING = 30
PERCENT_EMBEDDING_START = 35
PERCENT_EMBEDDING_END = 85
PERCENT_INDEXING = 90

#: Passages per embedding request when progress is being reported. The
#: provider accepts far more in one call, which is what an unreported caller
#: still does; slicing trades a few extra round trips for a bar that moves.
EMBED_SLICE = 64


async def report_progress(
    progress: ProgressCallback | None, stage: IngestionStage, percent: int, detail: str | None
) -> None:
    """Never lets progress reporting affect the ingestion itself."""
    if progress is None:
        return
    try:
        await progress(stage, percent, detail)
    except Exception:
        logger.warning("could not report ingestion progress (%s)", stage.value)


@dataclass(frozen=True, slots=True)
class IndexingTarget:
    """Which document, in which knowledge base, in which vector namespace."""

    tenant_id: UUID
    knowledge_base_id: UUID
    document_id: UUID
    vector_namespace: str


async def index_blocks(
    session: AsyncSession,
    *,
    target: IndexingTarget,
    blocks: list[ParsedBlock],
    chunker: TokenAwareChunker,
    embedding_client: EmbeddingClient,
    vector_search: VectorSearchClient,
    usage: TokenUsage | None = None,
    progress: ProgressCallback | None = None,
) -> int:
    """Chunks, embeds and indexes ``blocks``. Returns the chunk count.

    Safe to run twice: it clears the document's previous chunks and vectors
    before writing, so a redelivered job replaces rather than accumulates.

    ``usage``, when given, is filled with what the embedding calls cost. It is
    passed in rather than returned because the cost is spent the moment the
    provider answers -- a caller whose transaction later rolls back still owes
    it, and must be able to record it after the unwind.

    ``progress``, when given, is told as each stage starts, and the
    embeddings are requested in slices so that stage can report how far it
    has got. Without it the behaviour is exactly as before.
    """
    await report_progress(progress, IngestionStage.CHUNKING, PERCENT_CHUNKING, None)
    chunks = chunker.chunk(blocks)

    # Clear any previous attempt *before* writing, so a redelivered job
    # replaces rather than accumulates. Both stores, because they can disagree
    # if an earlier run died between them.
    await session.execute(
        text("DELETE FROM document_chunks WHERE document_id = :did"),
        {"did": str(target.document_id)},
    )
    await vector_search.delete_document(
        namespace=target.vector_namespace, document_id=target.document_id
    )

    if not chunks:
        # Nothing was indexed, so nothing is searchable. Whether that is a
        # *failure* depends on the caller -- one navigation-only page in a
        # 500-page crawl is not, an uploaded file the tenant expects to search
        # is -- so this reports the fact and lets each caller decide.
        #
        # It previously logged and returned 0 with a comment calling that "a
        # legitimate outcome", and `process_document_upload` then marked the
        # document `ready`. A 40-page scanned PDF whose OCR ran out of memory
        # was therefore recorded as successfully ingested, with zero chunks and
        # no error anywhere: the one state that looks like success and cannot
        # answer a single question.
        logger.info("document %s produced no chunks", target.document_id)
        return 0

    await vector_search.ensure_namespace(
        namespace=target.vector_namespace, dimensions=embedding_client.dimensions
    )
    texts = [c.text for c in chunks]
    if progress is None:
        embeddings = await embedding_client.embed_batch(texts, usage=usage)
    else:
        embeddings = []
        total = len(texts)
        for start in range(0, total, EMBED_SLICE):
            done = start
            await report_progress(
                progress,
                IngestionStage.EMBEDDING,
                PERCENT_EMBEDDING_START
                + (PERCENT_EMBEDDING_END - PERCENT_EMBEDDING_START) * done // total,
                f"{done} of {total} passages",
            )
            embeddings.extend(
                await embedding_client.embed_batch(texts[start : start + EMBED_SLICE], usage=usage)
            )
        await report_progress(
            progress,
            IngestionStage.INDEXING,
            PERCENT_INDEXING,
            f"Saving {total} passage{'s' if total != 1 else ''}",
        )

    vector_chunks: list[VectorChunk] = []
    # `strict=True` is load-bearing: a mismatch between chunks and embeddings
    # would otherwise silently truncate, indexing some chunks under another
    # chunk's vector -- wrong answers with no error anywhere.
    for index, (chunk, embedding) in enumerate(zip(chunks, embeddings, strict=True)):
        chunk_id = uuid4()
        await session.execute(
            text(
                "INSERT INTO document_chunks "
                "(id, tenant_id, knowledge_base_id, document_id, chunk_index, "
                " content, token_count, source_location) "
                "VALUES (:id, :tid, :kbid, :did, :idx, :content, :tokens, :loc)"
            ),
            {
                "id": str(chunk_id),
                "tid": str(target.tenant_id),
                "kbid": str(target.knowledge_base_id),
                "did": str(target.document_id),
                "idx": index,
                "content": chunk.text,
                "tokens": chunk.token_count,
                "loc": chunk.source_location,
            },
        )
        vector_chunks.append(
            VectorChunk(
                chunk_id=chunk_id,
                document_id=target.document_id,
                knowledge_base_id=target.knowledge_base_id,
                text=chunk.text,
                embedding=embedding,
                metadata={"source_location": chunk.source_location or ""},
            )
        )

    await vector_search.upsert(namespace=target.vector_namespace, chunks=vector_chunks)
    return len(vector_chunks)


async def record_ingestion_usage(
    ledger: UsageLedger | None,
    *,
    tenant_id: UUID,
    usage: TokenUsage,
    occurred_at: datetime,
) -> None:
    """One ledger row for one document's embeddings. Never raises.

    Called by each job **after** its transaction has finished, successful or
    not: the provider bills the embeddings the moment it returns them, so a
    document that then failed to index still cost what it cost. Recording
    inside the job's transaction would let the very failure being handled
    roll the record away.

    Fails open. The document's outcome is already decided; an error here
    would only replace it with a bookkeeping one, and the loss is a
    slightly-low figure on a dashboard, not an uncapped allowance -- ingestion
    is not part of any enforced limit.
    """
    await record_embedding_usage(
        ledger,
        tenant_id=tenant_id,
        channel=INGESTION_CHANNEL,
        usage=usage,
        occurred_at=occurred_at,
    )
