"""Qdrant-backed ``VectorSearchClient`` -- the real vector store.

Replaces ``InMemoryVectorSearchClient``, which existed only to prove the
*namespace* reaching a vector store is server-derived (Phase 7). This keeps
that property and adds the actual similarity search.

**Tenant isolation.** One collection per tenant
(``namespaces.collection_name_for_tenant``), with ``knowledge_base_id`` as an
indexed payload filter inside it. Every public method derives its collection
from the ``namespace`` argument, which callers obtained from an
already-authorized knowledge base's stored ``vector_namespace``. There is no
method that takes a raw collection name, and none that queries across
collections -- so a cross-tenant read has no expressible form here, rather
than being prevented by a check that could be forgotten.

**Distance metric.** Cosine, because OpenAI's embeddings are L2-normalized;
cosine and dot product rank identically on normalized vectors, and cosine
keeps scores in a readable [-1, 1] regardless of what a future model does.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from iam_platform.application.ai_resources.ports import (
    RetrievedChunk,
    TokenUsage,
    VectorChunk,
)
from iam_platform.infrastructure.vector.namespaces import (
    collection_name_for_tenant,
    parse_namespace,
)

if TYPE_CHECKING:
    from iam_platform.application.ai_resources.ports import EmbeddingClient
    from iam_platform.core.config import QdrantSettings

_KNOWLEDGE_BASE_FIELD = "knowledge_base_id"
_DOCUMENT_FIELD = "document_id"


class QdrantVectorSearchClient:
    def __init__(
        self,
        settings: QdrantSettings,
        embedding_client: EmbeddingClient,
        *,
        client: Any | None = None,
    ) -> None:
        self._embedding_client = embedding_client

        if client is not None:
            self._client: Any = client
            return

        from qdrant_client import AsyncQdrantClient

        api_key = settings.api_key.get_secret_value()
        self._client = AsyncQdrantClient(
            url=settings.url,
            # Passing an empty string as an API key makes Qdrant reject the
            # request outright; a self-hosted instance without auth expects
            # the header to be absent entirely.
            api_key=api_key or None,
            timeout=int(settings.timeout_seconds),
        )

    async def ensure_namespace(self, *, namespace: str, dimensions: int) -> None:
        from qdrant_client.models import Distance, PayloadSchemaType, VectorParams

        parsed = parse_namespace(namespace)
        collection = collection_name_for_tenant(parsed.tenant_id)

        if await self._client.collection_exists(collection):
            return

        await self._client.create_collection(
            collection_name=collection,
            vectors_config=VectorParams(size=dimensions, distance=Distance.COSINE),
        )
        # Without an index on these, Qdrant falls back to a full scan for
        # every filtered query -- correct, but linear in the tenant's whole
        # corpus rather than the one knowledge base being searched.
        for field in (_KNOWLEDGE_BASE_FIELD, _DOCUMENT_FIELD):
            await self._client.create_payload_index(
                collection_name=collection,
                field_name=field,
                field_schema=PayloadSchemaType.KEYWORD,
            )

    async def upsert(self, *, namespace: str, chunks: list[VectorChunk]) -> None:
        from qdrant_client.models import PointStruct

        if not chunks:
            return

        parsed = parse_namespace(namespace)
        collection = collection_name_for_tenant(parsed.tenant_id)

        points = [
            PointStruct(
                id=str(chunk.chunk_id),
                vector=chunk.embedding,
                payload={
                    # Written from the chunk, not from the namespace: these
                    # are what later filters match on, so they must describe
                    # the chunk itself.
                    _KNOWLEDGE_BASE_FIELD: str(chunk.knowledge_base_id),
                    _DOCUMENT_FIELD: str(chunk.document_id),
                    "text": chunk.text,
                    **chunk.metadata,
                },
            )
            for chunk in chunks
        ]
        await self._client.upsert(collection_name=collection, points=points, wait=True)

    async def delete_document(self, *, namespace: str, document_id: UUID) -> None:
        from qdrant_client.models import FieldCondition, Filter, FilterSelector, MatchValue

        parsed = parse_namespace(namespace)
        collection = collection_name_for_tenant(parsed.tenant_id)

        # A tenant with no collection yet has nothing to purge; treating that
        # as success is what makes the operation re-runnable after a crash.
        if not await self._client.collection_exists(collection):
            return

        await self._client.delete(
            collection_name=collection,
            points_selector=FilterSelector(
                filter=Filter(
                    must=[
                        # Both conditions, not just document_id: document ids
                        # are unique, but scoping the delete to the knowledge
                        # base too means a mismatched pair deletes nothing
                        # instead of reaching across knowledge bases.
                        FieldCondition(
                            key=_KNOWLEDGE_BASE_FIELD,
                            match=MatchValue(value=str(parsed.knowledge_base_id)),
                        ),
                        FieldCondition(
                            key=_DOCUMENT_FIELD, match=MatchValue(value=str(document_id))
                        ),
                    ]
                )
            ),
            wait=True,
        )

    async def query(
        self, *, namespace: str, query_text: str, top_k: int
    ) -> list[tuple[UUID, float]]:
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        parsed = parse_namespace(namespace)
        collection = collection_name_for_tenant(parsed.tenant_id)

        # Fail closed: a tenant that has never ingested anything has no
        # collection, and "no results" is the correct answer -- not an error
        # that a caller might be tempted to treat as retryable.
        if not await self._client.collection_exists(collection):
            return []

        query_vector = await self._embedding_client.embed(query_text)

        response = await self._client.query_points(
            collection_name=collection,
            query=query_vector,
            query_filter=Filter(
                must=[
                    FieldCondition(
                        key=_KNOWLEDGE_BASE_FIELD,
                        match=MatchValue(value=str(parsed.knowledge_base_id)),
                    )
                ]
            ),
            # Over-fetch: this port returns *documents*, and several chunks of
            # the same document routinely rank in the same result set. Asking
            # for exactly top_k points would collapse to fewer than top_k
            # distinct documents. The multiplier is a pragmatic bound, not a
            # guarantee -- callers get "up to top_k".
            limit=top_k * 4,
            with_payload=True,
        )

        return self._to_document_hits(response.points, top_k)

    async def search_chunks(
        self,
        *,
        namespace: str,
        query_text: str,
        top_k: int,
        usage: TokenUsage | None = None,
    ) -> list[RetrievedChunk]:
        """Chunk-level search. No document collapse -- see the port's docstring.

        Note there is no over-fetch multiplier here, unlike `query`: that one
        over-fetches because collapsing chunks to documents loses rows. This
        returns what it asks for, so `top_k` means `top_k`.
        """
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        parsed = parse_namespace(namespace)
        collection = collection_name_for_tenant(parsed.tenant_id)

        # Fail closed, same as `query`: a tenant with nothing ingested has no
        # collection, and "no passages" is the correct answer.
        if not await self._client.collection_exists(collection):
            return []

        # The query embedding is a real, billable call -- passing the meter
        # through is what stops it being invisible in every usage figure.
        query_vector = await self._embedding_client.embed(query_text, usage=usage)

        response = await self._client.query_points(
            collection_name=collection,
            query=query_vector,
            # The knowledge-base filter is not optional and not caller-supplied
            # as a raw value: it comes from the namespace the caller was
            # already authorized for. That is what stops a crafted request
            # reading another knowledge base's passages.
            query_filter=Filter(
                must=[
                    FieldCondition(
                        key=_KNOWLEDGE_BASE_FIELD,
                        match=MatchValue(value=str(parsed.knowledge_base_id)),
                    )
                ]
            ),
            limit=top_k,
            with_payload=True,
        )

        chunks: list[RetrievedChunk] = []
        for point in response.points:
            payload = point.payload or {}
            text = payload.get("text")
            document_id = payload.get(_DOCUMENT_FIELD)
            # A point missing its text or document id cannot be cited, and an
            # uncitable passage must not reach a generator that is required to
            # ground every claim. Skipped rather than passed along empty.
            if not isinstance(text, str) or not document_id:
                continue
            chunks.append(
                RetrievedChunk(
                    chunk_id=UUID(str(point.id)),
                    document_id=UUID(str(document_id)),
                    text=text,
                    score=float(point.score),
                    source_location=payload.get("source_location") or None,
                )
            )
        return chunks

    @staticmethod
    def _to_document_hits(points: list[Any], top_k: int) -> list[tuple[UUID, float]]:
        """Collapses chunk hits to their best-scoring document, preserving rank.

        Qdrant returns points in descending score order, so the first time a
        document is seen carries its highest score.
        """
        best: dict[UUID, float] = {}
        for point in points:
            payload = point.payload or {}
            raw_document_id = payload.get(_DOCUMENT_FIELD)
            if raw_document_id is None:
                continue
            document_id = UUID(str(raw_document_id))
            if document_id not in best:
                best[document_id] = float(point.score)
            if len(best) >= top_k:
                break
        return list(best.items())
