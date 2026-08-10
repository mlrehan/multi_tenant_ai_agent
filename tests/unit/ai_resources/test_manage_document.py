"""Retrying and deleting a single document.

Two properties carry the weight here, and neither is obvious from reading the
use cases:

1. **A delete must reach all three stores.** Vector points are the one that
   matters -- an orphaned point still matches a search, so a "deleted"
   document goes on answering questions and citing a source the tenant was
   told is gone. Asserting only that the row is soft-deleted would pass with
   the vector delete removed entirely.
2. **Authorizing the knowledge base is not authorizing the document.** The
   repository is RLS-scoped, so a cross-*tenant* id is already invisible; what
   the use cases add is the cross-*knowledge-base* check inside one tenant,
   which RLS cannot see. That check is what these tests pin down.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from iam_platform.application.ai_resources.exceptions import (
    DocumentNotFoundError,
    PermissionDeniedError,
)
from iam_platform.application.ai_resources.manage_document import (
    MAX_CHUNKS_PER_PAGE,
    DeleteDocument,
    DocumentActionCommand,
    GetDocumentDetail,
    GetDocumentDetailQuery,
    RetryDocumentIngestion,
)
from iam_platform.application.ai_resources.ports import StoredChunk
from iam_platform.core.clock import FixedClock
from iam_platform.domain.ai_resources.entities import (
    Document,
    DocumentStatus,
    KnowledgeBase,
    ResourceVisibility,
)
from iam_platform.domain.tenancy.entities import MembershipStatus, TenantMembership
from tests.unit.ai_resources.fakes import (
    FakeAiResourceUnitOfWork,
    FakeDocumentIngestionQueue,
    FakeObjectStorageClient,
    FakeVectorSearchClient,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)
MANAGE = "tenant.documents.upload"


def _seed_member(uow: FakeAiResourceUnitOfWork, tenant_id):
    user_id = uuid4()
    membership = TenantMembership(
        id=uuid4(),
        tenant_id=tenant_id,
        user_id=user_id,
        status=MembershipStatus.ACTIVE,
        created_at=NOW,
        updated_at=NOW,
    )
    uow.tenant_memberships.by_id[membership.id] = membership
    return user_id, membership


def _seed_knowledge_base(uow: FakeAiResourceUnitOfWork, tenant_id, owner_membership_id):
    kb = KnowledgeBase(
        id=uuid4(),
        tenant_id=tenant_id,
        name="kb",
        owner_membership_id=owner_membership_id,
        visibility=ResourceVisibility.TENANT,
        vector_namespace=f"{tenant_id}/{uuid4()}",
        created_at=NOW,
        updated_at=NOW,
    )
    uow.knowledge_bases.by_id[kb.id] = kb
    return kb


def _seed_document(
    uow: FakeAiResourceUnitOfWork,
    kb: KnowledgeBase,
    *,
    status: DocumentStatus = DocumentStatus.FAILED,
    failure_reason: str | None = "it went wrong",
    chunks: int = 0,
) -> Document:
    document = Document(
        id=uuid4(),
        tenant_id=kb.tenant_id,
        knowledge_base_id=kb.id,
        uploaded_by_membership_id=kb.owner_membership_id,
        filename="handbook.pdf",
        content_type="application/pdf",
        size_bytes=1024,
        checksum="abc123",
        storage_path=f"{kb.tenant_id}/{kb.id}/doc",
        status=status,
        failure_reason=failure_reason,
        created_at=NOW,
    )
    uow.documents.by_id[document.id] = document
    uow.documents.chunks[document.id] = chunks
    return document


def _command(user_id, tenant_id, kb_id, document_id, *, permissions=frozenset({MANAGE})):
    return DocumentActionCommand(
        actor_user_id=str(user_id),
        tenant_id=str(tenant_id),
        knowledge_base_id=str(kb_id),
        document_id=str(document_id),
        permissions=permissions,
    )


class TestRetryDocumentIngestion:
    async def test_a_failed_document_goes_back_to_processing_and_is_re_enqueued(
        self,
    ) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, membership = _seed_member(uow, tenant_id)
        kb = _seed_knowledge_base(uow, tenant_id, membership.id)
        document = _seed_document(uow, kb)
        queue = FakeDocumentIngestionQueue()

        await RetryDocumentIngestion(uow, queue, FixedClock(NOW)).execute(
            _command(user_id, tenant_id, kb.id, document.id)
        )

        stored = await uow.documents.get_by_id(document.id)
        assert stored is not None
        assert stored.status is DocumentStatus.PROCESSING
        # Cleared, not merely overwritten on the next success: the console
        # renders the reason whenever one is present, so a stale reason on a
        # document that is visibly retrying reads as a fresh failure.
        assert stored.failure_reason is None
        assert queue.enqueued == [(tenant_id, document.id)]
        # The worker re-validates this identity per job, so an enqueue that
        # dropped it would make that check impossible.
        assert queue.enqueued_actors == [user_id]

    async def test_retry_without_the_permission_is_refused(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, membership = _seed_member(uow, tenant_id)
        kb = _seed_knowledge_base(uow, tenant_id, membership.id)
        document = _seed_document(uow, kb)
        queue = FakeDocumentIngestionQueue()

        with pytest.raises(PermissionDeniedError):
            await RetryDocumentIngestion(uow, queue, FixedClock(NOW)).execute(
                _command(
                    user_id, tenant_id, kb.id, document.id, permissions=frozenset()
                )
            )

        assert queue.enqueued == []
        stored = await uow.documents.get_by_id(document.id)
        assert stored is not None
        assert stored.status is DocumentStatus.FAILED

    async def test_a_document_in_another_knowledge_base_is_not_found(self) -> None:
        """Same tenant, so RLS permits the read -- only the explicit check
        stops the caller acting on a document they did not authorize."""
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, membership = _seed_member(uow, tenant_id)
        authorized_kb = _seed_knowledge_base(uow, tenant_id, membership.id)
        other_kb = _seed_knowledge_base(uow, tenant_id, membership.id)
        document = _seed_document(uow, other_kb)
        queue = FakeDocumentIngestionQueue()

        with pytest.raises(DocumentNotFoundError):
            await RetryDocumentIngestion(uow, queue, FixedClock(NOW)).execute(
                _command(user_id, tenant_id, authorized_kb.id, document.id)
            )

        assert queue.enqueued == []

    async def test_an_already_deleted_document_cannot_be_retried(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, membership = _seed_member(uow, tenant_id)
        kb = _seed_knowledge_base(uow, tenant_id, membership.id)
        document = _seed_document(uow, kb)
        document.soft_delete(now=NOW)
        queue = FakeDocumentIngestionQueue()

        with pytest.raises(DocumentNotFoundError):
            await RetryDocumentIngestion(uow, queue, FixedClock(NOW)).execute(
                _command(user_id, tenant_id, kb.id, document.id)
            )

        assert queue.enqueued == []


class TestDeleteDocument:
    async def test_delete_removes_vectors_chunks_bytes_and_the_row(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, membership = _seed_member(uow, tenant_id)
        kb = _seed_knowledge_base(uow, tenant_id, membership.id)
        document = _seed_document(uow, kb, status=DocumentStatus.READY, chunks=3)
        storage = FakeObjectStorageClient()
        await storage.put(path=document.storage_path, data=b"bytes", content_type="application/pdf")
        vectors = FakeVectorSearchClient()

        await DeleteDocument(uow, storage, vectors, FixedClock(NOW)).execute(
            _command(user_id, tenant_id, kb.id, document.id)
        )

        # The namespace is the one stored on the authorized knowledge base --
        # never anything derived from the request.
        assert vectors.deleted == [(kb.vector_namespace, document.id)]
        assert await uow.documents.count_chunks(document.id) == 0
        assert storage.objects == {}
        stored = await uow.documents.get_by_id(document.id)
        assert stored is not None
        assert stored.is_deleted

    async def test_delete_still_completes_when_object_storage_fails(self) -> None:
        """Best-effort bytes: the searchable copies are already gone, and
        refusing the whole delete would leave the tenant with a document they
        cannot remove."""
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, membership = _seed_member(uow, tenant_id)
        kb = _seed_knowledge_base(uow, tenant_id, membership.id)
        document = _seed_document(uow, kb, status=DocumentStatus.READY, chunks=2)
        vectors = FakeVectorSearchClient()

        class ExplodingStorage(FakeObjectStorageClient):
            async def delete(self, *, path: str) -> None:
                raise OSError("bucket unreachable")

        await DeleteDocument(uow, ExplodingStorage(), vectors, FixedClock(NOW)).execute(
            _command(user_id, tenant_id, kb.id, document.id)
        )

        stored = await uow.documents.get_by_id(document.id)
        assert stored is not None
        assert stored.is_deleted
        assert vectors.deleted == [(kb.vector_namespace, document.id)]
        assert await uow.documents.count_chunks(document.id) == 0

    async def test_delete_without_the_permission_leaves_everything_intact(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, membership = _seed_member(uow, tenant_id)
        kb = _seed_knowledge_base(uow, tenant_id, membership.id)
        document = _seed_document(uow, kb, status=DocumentStatus.READY, chunks=3)
        storage = FakeObjectStorageClient()
        await storage.put(path=document.storage_path, data=b"bytes", content_type="application/pdf")
        vectors = FakeVectorSearchClient()

        with pytest.raises(PermissionDeniedError):
            await DeleteDocument(uow, storage, vectors, FixedClock(NOW)).execute(
                _command(
                    user_id, tenant_id, kb.id, document.id, permissions=frozenset()
                )
            )

        assert vectors.deleted == []
        assert await uow.documents.count_chunks(document.id) == 3
        assert storage.objects != {}
        stored = await uow.documents.get_by_id(document.id)
        assert stored is not None
        assert not stored.is_deleted

    async def test_a_document_in_another_knowledge_base_is_not_found(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, membership = _seed_member(uow, tenant_id)
        authorized_kb = _seed_knowledge_base(uow, tenant_id, membership.id)
        other_kb = _seed_knowledge_base(uow, tenant_id, membership.id)
        document = _seed_document(uow, other_kb, status=DocumentStatus.READY, chunks=4)
        vectors = FakeVectorSearchClient()

        with pytest.raises(DocumentNotFoundError):
            await DeleteDocument(
                uow, FakeObjectStorageClient(), vectors, FixedClock(NOW)
            ).execute(_command(user_id, tenant_id, authorized_kb.id, document.id))

        # Nothing was touched in the other knowledge base -- in particular no
        # vector delete was issued, which would have been silent data loss.
        assert vectors.deleted == []
        assert await uow.documents.count_chunks(document.id) == 4
        stored = await uow.documents.get_by_id(document.id)
        assert stored is not None
        assert not stored.is_deleted


class TestGetDocumentDetail:
    """The extracted text, which is the only thing that answers "why does this
    document never come up?" -- status and a count say something went wrong,
    the text says what."""

    def _seed_chunks(self, uow: FakeAiResourceUnitOfWork, document: Document, count: int):
        rows = [
            StoredChunk(
                chunk_id=uuid4(),
                chunk_index=i,
                text=f"passage {i}",
                token_count=10 + i,
                source_location=f"page {i + 1}",
            )
            for i in range(count)
        ]
        uow.documents.chunk_rows[document.id] = rows
        uow.documents.chunks[document.id] = count
        return rows

    async def test_detail_returns_the_indexed_text_in_document_order(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, membership = _seed_member(uow, tenant_id)
        kb = _seed_knowledge_base(uow, tenant_id, membership.id)
        document = _seed_document(uow, kb, status=DocumentStatus.READY)
        self._seed_chunks(uow, document, 3)

        detail = await GetDocumentDetail(uow).execute(
            GetDocumentDetailQuery(
                actor_user_id=str(user_id),
                tenant_id=str(tenant_id),
                knowledge_base_id=str(kb.id),
                document_id=str(document.id),
                permissions=frozenset({MANAGE}),
            )
        )

        assert [c.chunk_index for c in detail.chunks] == [0, 1, 2]
        assert [c.text for c in detail.chunks] == ["passage 0", "passage 1", "passage 2"]
        assert [c.source_location for c in detail.chunks] == ["page 1", "page 2", "page 3"]
        assert detail.chunk_count == 3

    async def test_chunk_count_is_the_document_total_not_the_page_length(self) -> None:
        """Otherwise a reader on page one of a 90-chunk document is told the
        document has 10 chunks."""
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, membership = _seed_member(uow, tenant_id)
        kb = _seed_knowledge_base(uow, tenant_id, membership.id)
        document = _seed_document(uow, kb, status=DocumentStatus.READY)
        self._seed_chunks(uow, document, 90)

        detail = await GetDocumentDetail(uow).execute(
            GetDocumentDetailQuery(
                actor_user_id=str(user_id),
                tenant_id=str(tenant_id),
                knowledge_base_id=str(kb.id),
                document_id=str(document.id),
                permissions=frozenset({MANAGE}),
                limit=10,
                offset=20,
            )
        )

        assert len(detail.chunks) == 10
        assert [c.chunk_index for c in detail.chunks] == list(range(20, 30))
        assert detail.chunk_count == 90

    async def test_an_over_large_limit_is_capped_rather_than_refused(self) -> None:
        """A caller asking for everything gets a bounded page, not a 400 and
        not a response carrying hundreds of passages."""
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, membership = _seed_member(uow, tenant_id)
        kb = _seed_knowledge_base(uow, tenant_id, membership.id)
        document = _seed_document(uow, kb, status=DocumentStatus.READY)
        self._seed_chunks(uow, document, 400)

        detail = await GetDocumentDetail(uow).execute(
            GetDocumentDetailQuery(
                actor_user_id=str(user_id),
                tenant_id=str(tenant_id),
                knowledge_base_id=str(kb.id),
                document_id=str(document.id),
                permissions=frozenset({MANAGE}),
                limit=10_000,
            )
        )

        assert len(detail.chunks) == MAX_CHUNKS_PER_PAGE

    async def test_a_zero_chunk_document_reports_no_passages(self) -> None:
        """The state the whole chunk-count surface exists for: `ready`, and
        nothing in it to find."""
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, membership = _seed_member(uow, tenant_id)
        kb = _seed_knowledge_base(uow, tenant_id, membership.id)
        document = _seed_document(uow, kb, status=DocumentStatus.READY, chunks=0)

        detail = await GetDocumentDetail(uow).execute(
            GetDocumentDetailQuery(
                actor_user_id=str(user_id),
                tenant_id=str(tenant_id),
                knowledge_base_id=str(kb.id),
                document_id=str(document.id),
                permissions=frozenset({MANAGE}),
            )
        )

        assert detail.chunks == []
        assert detail.chunk_count == 0

    async def test_read_access_is_enough_no_upload_permission_needed(self) -> None:
        """Deliberate: the same passages are already reachable by asking the
        knowledge base a question, so requiring modify rights would withhold
        the diagnosis while protecting nothing."""
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, membership = _seed_member(uow, tenant_id)
        kb = _seed_knowledge_base(uow, tenant_id, membership.id)
        document = _seed_document(uow, kb, status=DocumentStatus.READY)
        self._seed_chunks(uow, document, 2)

        detail = await GetDocumentDetail(uow).execute(
            GetDocumentDetailQuery(
                actor_user_id=str(user_id),
                tenant_id=str(tenant_id),
                knowledge_base_id=str(kb.id),
                document_id=str(document.id),
                permissions=frozenset(),
            )
        )

        assert len(detail.chunks) == 2

    async def test_a_document_in_another_knowledge_base_is_not_found(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, membership = _seed_member(uow, tenant_id)
        authorized_kb = _seed_knowledge_base(uow, tenant_id, membership.id)
        other_kb = _seed_knowledge_base(uow, tenant_id, membership.id)
        document = _seed_document(uow, other_kb, status=DocumentStatus.READY)
        self._seed_chunks(uow, document, 5)

        with pytest.raises(DocumentNotFoundError):
            await GetDocumentDetail(uow).execute(
                GetDocumentDetailQuery(
                    actor_user_id=str(user_id),
                    tenant_id=str(tenant_id),
                    knowledge_base_id=str(authorized_kb.id),
                    document_id=str(document.id),
                    permissions=frozenset({MANAGE}),
                )
            )
