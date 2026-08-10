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
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from iam_platform.application.ai_resources.ports import (
    EmbeddingClient,
    ParsedBlock,
    VectorChunk,
    VectorSearchClient,
)
from iam_platform.infrastructure.parsing.chunking import TokenAwareChunker

logger = logging.getLogger("iam_platform.workers.jobs.indexing")


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
) -> int:
    """Chunks, embeds and indexes ``blocks``. Returns the chunk count.

    Safe to run twice: it clears the document's previous chunks and vectors
    before writing, so a redelivered job replaces rather than accumulates.
    """
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
    embeddings = await embedding_client.embed_batch([c.text for c in chunks])

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
