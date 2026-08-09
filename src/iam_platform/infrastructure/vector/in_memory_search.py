"""In-memory ``VectorSearchClient`` -- a faithful fake, for tests.

Upgraded from the Phase 7 stub (which only recorded seeded results) into a
real implementation: it stores chunks, filters by knowledge base, computes
actual cosine similarity, and collapses chunk hits to documents exactly as the
Qdrant client does. That fidelity is the point -- a fake that returns
pre-seeded answers can only prove which namespace was passed, whereas this one
lets the ingestion pipeline's tests assert that what was written is what comes
back.

**Not a production fallback.** ``bootstrap`` never substitutes this for a
missing Qdrant: an unconfigured deployment gets
``UnconfiguredVectorSearchClient``, which raises. Returning an empty result
set from a store that was never written to would be indistinguishable from a
genuine "no matches", and a silently-empty knowledge base is precisely the
kind of inert failure this project has been bitten by before.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING
from uuid import UUID

from iam_platform.application.ai_resources.ports import RetrievedChunk, VectorChunk
from iam_platform.infrastructure.vector.namespaces import (
    collection_name_for_tenant,
    parse_namespace,
)

if TYPE_CHECKING:
    from iam_platform.application.ai_resources.ports import EmbeddingClient


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise ValueError(
            f"vector dimension mismatch: stored {len(right)}, query {len(left)} -- "
            "the collection was provisioned for a different embedding model"
        )
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


class InMemoryVectorSearchClient:
    def __init__(self, embedding_client: EmbeddingClient | None = None) -> None:
        # Keyed by collection, mirroring Qdrant's tenant-per-collection layout
        # so a test that expects cross-tenant isolation exercises the same
        # structure production relies on.
        self._collections: dict[str, dict[UUID, VectorChunk]] = {}
        self._dimensions: dict[str, int] = {}
        self._embedding_client = embedding_client
        self.queried_namespaces: list[str] = []

    async def ensure_namespace(self, *, namespace: str, dimensions: int) -> None:
        parsed = parse_namespace(namespace)
        collection = collection_name_for_tenant(parsed.tenant_id)
        self._collections.setdefault(collection, {})
        self._dimensions.setdefault(collection, dimensions)

    async def upsert(self, *, namespace: str, chunks: list[VectorChunk]) -> None:
        if not chunks:
            return
        parsed = parse_namespace(namespace)
        collection = collection_name_for_tenant(parsed.tenant_id)
        stored = self._collections.setdefault(collection, {})
        for chunk in chunks:
            # Keyed by chunk_id, so re-ingesting overwrites rather than
            # duplicating -- same contract as the real client.
            stored[chunk.chunk_id] = chunk

    async def delete_document(self, *, namespace: str, document_id: UUID) -> None:
        parsed = parse_namespace(namespace)
        collection = collection_name_for_tenant(parsed.tenant_id)
        stored = self._collections.get(collection)
        if stored is None:
            return
        for chunk_id in [
            chunk_id
            for chunk_id, chunk in stored.items()
            if chunk.document_id == document_id
            and chunk.knowledge_base_id == parsed.knowledge_base_id
        ]:
            del stored[chunk_id]

    async def query(
        self, *, namespace: str, query_text: str, top_k: int
    ) -> list[tuple[UUID, float]]:
        self.queried_namespaces.append(namespace)
        parsed = parse_namespace(namespace)
        collection = collection_name_for_tenant(parsed.tenant_id)
        stored = self._collections.get(collection)
        if not stored:
            return []

        if self._embedding_client is None:
            raise RuntimeError(
                "InMemoryVectorSearchClient needs an embedding client to answer queries; "
                "construct it with one, or assert on upsert()/delete_document() instead"
            )
        query_vector = await self._embedding_client.embed(query_text)

        scored = sorted(
            (
                (chunk, _cosine_similarity(query_vector, chunk.embedding))
                for chunk in stored.values()
                if chunk.knowledge_base_id == parsed.knowledge_base_id
            ),
            key=lambda pair: pair[1],
            reverse=True,
        )

        best: dict[UUID, float] = {}
        for chunk, score in scored:
            if chunk.document_id not in best:
                best[chunk.document_id] = score
            if len(best) >= top_k:
                break
        return list(best.items())

    async def search_chunks(
        self, *, namespace: str, query_text: str, top_k: int
    ) -> list[RetrievedChunk]:
        """Chunk-level search over the in-memory store.

        A faithful implementation, not a stub: it applies the same
        knowledge-base filter and the same cosine ranking as `query`, minus the
        document collapse. A fake that returned chunks in insertion order would
        let a reranking test pass while the retrieval it depends on was
        backwards.
        """
        self.queried_namespaces.append(namespace)
        parsed = parse_namespace(namespace)
        stored = self._collections.get(collection_name_for_tenant(parsed.tenant_id))
        if not stored:
            return []

        if self._embedding_client is None:
            raise RuntimeError(
                "InMemoryVectorSearchClient needs an embedding client to answer queries; "
                "construct it with one, or assert on upsert()/delete_document() instead"
            )
        query_vector = await self._embedding_client.embed(query_text)

        scored = sorted(
            (
                (chunk, _cosine_similarity(query_vector, chunk.embedding))
                for chunk in stored.values()
                if chunk.knowledge_base_id == parsed.knowledge_base_id
            ),
            key=lambda pair: pair[1],
            reverse=True,
        )
        return [
            RetrievedChunk(
                chunk_id=chunk.chunk_id,
                document_id=chunk.document_id,
                text=chunk.text,
                score=score,
                source_location=chunk.metadata.get("source_location") or None,
            )
            for chunk, score in scored[:top_k]
        ]
