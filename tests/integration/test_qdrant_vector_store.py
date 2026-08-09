"""Qdrant client proven against a real Qdrant, not a mock.

The property that matters here is **tenant isolation in the vector store**,
and it is exactly the kind that a mock cannot establish: a fake asserts that
the code passed the filter it was told to pass, whereas only a real store can
show that a query genuinely cannot see another tenant's vectors. This mirrors
``tests/integration/db/test_rls_isolation.py``, which proves the same property
for Postgres rather than trusting the application to filter correctly.

Requires the dev Qdrant (``docker compose -f docker-compose.dev.yml up -d
qdrant``) and reads ``QDRANT__URL`` the same way the app does.

Every test provisions its own tenant UUIDs, so collections never collide
between runs; the fixture drops what it created afterwards.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
import pytest_asyncio

from iam_platform.application.ai_resources.ports import VectorChunk
from iam_platform.core.config import Settings
from iam_platform.infrastructure.vector.namespaces import (
    TenantScopedVectorNamespaceFactory,
    collection_name_for_tenant,
)
from iam_platform.infrastructure.vector.qdrant_search import QdrantVectorSearchClient

pytestmark = pytest.mark.integration

_DIMENSIONS = 8


class _StubEmbeddingClient:
    """Deterministic, dimension-correct embeddings.

    Real embeddings would make assertions depend on model behaviour; the
    subject under test is the Qdrant adapter's filtering and collection
    routing, not embedding quality. Encodes a caller-chosen vector directly so
    tests control similarity ordering exactly.
    """

    def __init__(self) -> None:
        self.vectors: dict[str, list[float]] = {}

    @property
    def dimensions(self) -> int:
        return _DIMENSIONS

    async def embed(self, text: str) -> list[float]:
        return self.vectors.get(text, [1.0] + [0.0] * (_DIMENSIONS - 1))

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [await self.embed(text) for text in texts]


def _unit_vector(position: int) -> list[float]:
    vector = [0.0] * _DIMENSIONS
    vector[position] = 1.0
    return vector


@pytest_asyncio.fixture
async def qdrant() -> AsyncIterator[tuple[QdrantVectorSearchClient, _StubEmbeddingClient]]:
    settings = Settings()  # type: ignore[call-arg]
    embedding = _StubEmbeddingClient()
    client = QdrantVectorSearchClient(settings.qdrant, embedding)
    created: list[UUID] = []

    # Records tenants so teardown can drop their collections; wrapping
    # ensure_namespace is simpler than parsing them back out afterwards.
    original_ensure = client.ensure_namespace

    async def tracking_ensure(*, namespace: str, dimensions: int) -> None:
        created.append(UUID(namespace.split("/")[0]))
        await original_ensure(namespace=namespace, dimensions=dimensions)

    client.ensure_namespace = tracking_ensure  # type: ignore[method-assign]

    try:
        yield client, embedding
    finally:
        for tenant_id in created:
            collection = collection_name_for_tenant(tenant_id)
            if await client._client.collection_exists(collection):
                await client._client.delete_collection(collection)


def _chunk(
    *,
    knowledge_base_id: UUID,
    document_id: UUID,
    text: str,
    embedding: list[float],
) -> VectorChunk:
    return VectorChunk(
        chunk_id=uuid4(),
        document_id=document_id,
        knowledge_base_id=knowledge_base_id,
        text=text,
        embedding=embedding,
        metadata={"page": 1},
    )


class TestQdrantVectorSearchClient:
    async def test_upsert_then_query_returns_the_document(
        self, qdrant: tuple[QdrantVectorSearchClient, _StubEmbeddingClient]
    ) -> None:
        client, embedding = qdrant
        tenant_id, knowledge_base_id, document_id = uuid4(), uuid4(), uuid4()
        namespace = TenantScopedVectorNamespaceFactory().build(
            tenant_id=tenant_id, knowledge_base_id=knowledge_base_id
        )

        await client.ensure_namespace(namespace=namespace, dimensions=_DIMENSIONS)
        await client.upsert(
            namespace=namespace,
            chunks=[
                _chunk(
                    knowledge_base_id=knowledge_base_id,
                    document_id=document_id,
                    text="admission policy",
                    embedding=_unit_vector(0),
                )
            ],
        )
        embedding.vectors["what are the admission rules"] = _unit_vector(0)

        hits = await client.query(
            namespace=namespace, query_text="what are the admission rules", top_k=5
        )

        assert [document_id for document_id, _ in hits] == [document_id]

    async def test_a_tenant_cannot_see_another_tenants_vectors(
        self, qdrant: tuple[QdrantVectorSearchClient, _StubEmbeddingClient]
    ) -> None:
        """The isolation proof, against a live store.

        Both tenants index an identical vector, so similarity alone would
        return both. Only the collection boundary keeps them apart.
        """
        client, embedding = qdrant
        tenant_a, tenant_b = uuid4(), uuid4()
        kb_a, kb_b = uuid4(), uuid4()
        doc_a, doc_b = uuid4(), uuid4()
        factory = TenantScopedVectorNamespaceFactory()
        namespace_a = factory.build(tenant_id=tenant_a, knowledge_base_id=kb_a)
        namespace_b = factory.build(tenant_id=tenant_b, knowledge_base_id=kb_b)

        for namespace, kb_id, doc_id in (
            (namespace_a, kb_a, doc_a),
            (namespace_b, kb_b, doc_b),
        ):
            await client.ensure_namespace(namespace=namespace, dimensions=_DIMENSIONS)
            await client.upsert(
                namespace=namespace,
                chunks=[
                    _chunk(
                        knowledge_base_id=kb_id,
                        document_id=doc_id,
                        text="identical content",
                        embedding=_unit_vector(0),
                    )
                ],
            )
        embedding.vectors["identical content"] = _unit_vector(0)

        hits_a = await client.query(
            namespace=namespace_a, query_text="identical content", top_k=10
        )
        hits_b = await client.query(
            namespace=namespace_b, query_text="identical content", top_k=10
        )

        assert [d for d, _ in hits_a] == [doc_a]
        assert [d for d, _ in hits_b] == [doc_b]

    async def test_knowledge_bases_within_a_tenant_are_filtered_apart(
        self, qdrant: tuple[QdrantVectorSearchClient, _StubEmbeddingClient]
    ) -> None:
        """Second isolation boundary: one collection per *tenant* means two
        knowledge bases share storage, so the payload filter is what keeps a
        query scoped to the one the caller was authorized for."""
        client, embedding = qdrant
        tenant_id = uuid4()
        kb_one, kb_two = uuid4(), uuid4()
        doc_one, doc_two = uuid4(), uuid4()
        factory = TenantScopedVectorNamespaceFactory()
        namespace_one = factory.build(tenant_id=tenant_id, knowledge_base_id=kb_one)
        namespace_two = factory.build(tenant_id=tenant_id, knowledge_base_id=kb_two)

        await client.ensure_namespace(namespace=namespace_one, dimensions=_DIMENSIONS)
        for namespace, kb_id, doc_id in (
            (namespace_one, kb_one, doc_one),
            (namespace_two, kb_two, doc_two),
        ):
            await client.upsert(
                namespace=namespace,
                chunks=[
                    _chunk(
                        knowledge_base_id=kb_id,
                        document_id=doc_id,
                        text="shared",
                        embedding=_unit_vector(0),
                    )
                ],
            )
        embedding.vectors["shared"] = _unit_vector(0)

        hits = await client.query(namespace=namespace_one, query_text="shared", top_k=10)

        assert [d for d, _ in hits] == [doc_one]

    async def test_query_on_a_tenant_with_no_collection_returns_empty(
        self, qdrant: tuple[QdrantVectorSearchClient, _StubEmbeddingClient]
    ) -> None:
        """Fail closed: a tenant that has never ingested anything gets "no
        results", not an error a caller might retry."""
        client, _ = qdrant
        namespace = TenantScopedVectorNamespaceFactory().build(
            tenant_id=uuid4(), knowledge_base_id=uuid4()
        )

        assert await client.query(namespace=namespace, query_text="anything", top_k=5) == []

    async def test_ensure_namespace_is_idempotent(
        self, qdrant: tuple[QdrantVectorSearchClient, _StubEmbeddingClient]
    ) -> None:
        """Called before every ingestion job, so it runs constantly."""
        client, _ = qdrant
        namespace = TenantScopedVectorNamespaceFactory().build(
            tenant_id=uuid4(), knowledge_base_id=uuid4()
        )

        await client.ensure_namespace(namespace=namespace, dimensions=_DIMENSIONS)
        await client.ensure_namespace(namespace=namespace, dimensions=_DIMENSIONS)

    async def test_upsert_replaces_rather_than_duplicates(
        self, qdrant: tuple[QdrantVectorSearchClient, _StubEmbeddingClient]
    ) -> None:
        """Re-ingesting a document must not leave two copies of every chunk."""
        client, embedding = qdrant
        tenant_id, knowledge_base_id, document_id = uuid4(), uuid4(), uuid4()
        namespace = TenantScopedVectorNamespaceFactory().build(
            tenant_id=tenant_id, knowledge_base_id=knowledge_base_id
        )
        await client.ensure_namespace(namespace=namespace, dimensions=_DIMENSIONS)

        chunk = _chunk(
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
            text="v1",
            embedding=_unit_vector(0),
        )
        await client.upsert(namespace=namespace, chunks=[chunk])
        await client.upsert(namespace=namespace, chunks=[chunk])

        collection = collection_name_for_tenant(tenant_id)
        count = await client._client.count(collection_name=collection, exact=True)
        assert count.count == 1

    async def test_delete_document_removes_only_that_document(
        self, qdrant: tuple[QdrantVectorSearchClient, _StubEmbeddingClient]
    ) -> None:
        client, embedding = qdrant
        tenant_id, knowledge_base_id = uuid4(), uuid4()
        doc_keep, doc_remove = uuid4(), uuid4()
        namespace = TenantScopedVectorNamespaceFactory().build(
            tenant_id=tenant_id, knowledge_base_id=knowledge_base_id
        )
        await client.ensure_namespace(namespace=namespace, dimensions=_DIMENSIONS)
        await client.upsert(
            namespace=namespace,
            chunks=[
                _chunk(
                    knowledge_base_id=knowledge_base_id,
                    document_id=doc_keep,
                    text="keep",
                    embedding=_unit_vector(0),
                ),
                _chunk(
                    knowledge_base_id=knowledge_base_id,
                    document_id=doc_remove,
                    text="remove",
                    embedding=_unit_vector(1),
                ),
            ],
        )

        await client.delete_document(namespace=namespace, document_id=doc_remove)

        embedding.vectors["anything"] = _unit_vector(0)
        hits = await client.query(namespace=namespace, query_text="anything", top_k=10)
        assert [d for d, _ in hits] == [doc_keep]

    async def test_delete_document_is_idempotent(
        self, qdrant: tuple[QdrantVectorSearchClient, _StubEmbeddingClient]
    ) -> None:
        """A purge that crashed part-way must be safe to re-run."""
        client, _ = qdrant
        namespace = TenantScopedVectorNamespaceFactory().build(
            tenant_id=uuid4(), knowledge_base_id=uuid4()
        )

        # Both before the collection exists, and after.
        await client.delete_document(namespace=namespace, document_id=uuid4())
        await client.ensure_namespace(namespace=namespace, dimensions=_DIMENSIONS)
        await client.delete_document(namespace=namespace, document_id=uuid4())

    async def test_query_collapses_multiple_chunks_to_one_document(
        self, qdrant: tuple[QdrantVectorSearchClient, _StubEmbeddingClient]
    ) -> None:
        """The port returns documents, and a long document contributes many
        chunks -- without deduplication a single document would fill top_k."""
        client, embedding = qdrant
        tenant_id, knowledge_base_id = uuid4(), uuid4()
        big_document, other_document = uuid4(), uuid4()
        namespace = TenantScopedVectorNamespaceFactory().build(
            tenant_id=tenant_id, knowledge_base_id=knowledge_base_id
        )
        await client.ensure_namespace(namespace=namespace, dimensions=_DIMENSIONS)
        await client.upsert(
            namespace=namespace,
            chunks=[
                *[
                    _chunk(
                        knowledge_base_id=knowledge_base_id,
                        document_id=big_document,
                        text=f"part {i}",
                        embedding=_unit_vector(0),
                    )
                    for i in range(5)
                ],
                _chunk(
                    knowledge_base_id=knowledge_base_id,
                    document_id=other_document,
                    text="other",
                    embedding=_unit_vector(1),
                ),
            ],
        )
        embedding.vectors["query"] = _unit_vector(0)

        hits = await client.query(namespace=namespace, query_text="query", top_k=5)

        document_ids = [d for d, _ in hits]
        assert len(document_ids) == len(set(document_ids)), "same document returned twice"
        assert big_document in document_ids
