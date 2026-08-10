"""Managing a document *after* ingestion: retry it, or delete it.

Split from `manage_knowledge_base.py`, which is about the knowledge base as a
container -- creating one, listing them, uploading into one, querying one.
These two act on a single document that already exists, and both are things a
tenant admin needs precisely when ingestion went wrong.

**Both reuse the existing authorization path** (`build_requester_context` then
`load_visible_knowledge_base(..., for_modification=True)`) rather than
introducing a document-level permission. Changing what a knowledge base
contains is one authority, whether that means adding a file or removing one,
and a second permission that has to be granted alongside the first is a way to
end up with tenants who can upload but not clean up.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from iam_platform.application.ai_resources.authorize import load_visible_knowledge_base
from iam_platform.application.ai_resources.exceptions import (
    DocumentNotFoundError,
    KnowledgeBaseNotFoundError,
    PermissionDeniedError,
)
from iam_platform.application.ai_resources.ports import (
    AiResourceUowFactory,
    DocumentIngestionQueue,
    ObjectStorageClient,
    StoredChunk,
    VectorSearchClient,
)
from iam_platform.application.ai_resources.requester import build_requester_context
from iam_platform.core.clock import Clock
from iam_platform.domain.ai_resources.entities import Document

logger = logging.getLogger("iam_platform.application.ai_resources.manage_document")

#: Same authority as uploading. See the module docstring.
MANAGE_DOCUMENT_PERMISSION = "tenant.documents.upload"

#: Most chunks one request will return. A large PDF runs to hundreds, and this
#: is read by a person scrolling a dialog -- so the cap bounds the response
#: rather than expressing a limit anyone is meant to hit.
MAX_CHUNKS_PER_PAGE = 50


@dataclass(frozen=True, slots=True)
class DocumentActionCommand:
    actor_user_id: str
    tenant_id: str
    knowledge_base_id: str
    document_id: str
    permissions: frozenset[str]


@dataclass(frozen=True, slots=True)
class GetDocumentDetailQuery:
    actor_user_id: str
    tenant_id: str
    knowledge_base_id: str
    document_id: str
    permissions: frozenset[str]
    limit: int = MAX_CHUNKS_PER_PAGE
    offset: int = 0


@dataclass(frozen=True, slots=True)
class DocumentDetail:
    document: Document
    chunks: list[StoredChunk]
    #: Total across the whole document, not the length of `chunks` -- the
    #: caller needs to know there are 300 more to page through.
    chunk_count: int


class GetDocumentDetail:
    """One document with the text that was actually indexed from it.

    This is the answer to "why does this document never come up?", which is
    otherwise unanswerable from the console: `status` and a chunk count say
    *that* something went wrong, and only the extracted text says *what* --
    a scanned page that OCR'd into noise, a spreadsheet whose rows became one
    unreadable run, a crawled page that captured the cookie banner.

    **Read access is enough.** Deliberately `for_modification=False`: anyone
    who can see the knowledge base can already surface these exact passages by
    asking a question, so requiring modify rights to look at them directly
    would protect nothing and would keep the diagnosis from the people most
    likely to need it.
    """

    def __init__(self, uow_factory: AiResourceUowFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, query: GetDocumentDetailQuery) -> DocumentDetail:
        actor_id = UUID(query.actor_user_id)
        tenant_id = UUID(query.tenant_id)
        knowledge_base_id = UUID(query.knowledge_base_id)
        document_id = UUID(query.document_id)
        limit = max(1, min(query.limit, MAX_CHUNKS_PER_PAGE))
        offset = max(0, query.offset)

        async with self._uow_factory(actor_id, tenant_id) as uow:
            requester = await build_requester_context(
                uow, tenant_id=tenant_id, user_id=actor_id, permissions=query.permissions
            )
            if requester is None:
                raise KnowledgeBaseNotFoundError(query.knowledge_base_id)

            await load_visible_knowledge_base(
                uow,
                knowledge_base_id=knowledge_base_id,
                requester=requester,
                for_modification=False,
            )

            document = await uow.documents.get_by_id(document_id)
            # The same cross-knowledge-base check the mutating paths make, and
            # for the same reason: authorizing *a* knowledge base is not
            # authorizing a document that lives in a different one.
            if (
                document is None
                or document.is_deleted
                or document.knowledge_base_id != knowledge_base_id
            ):
                raise DocumentNotFoundError(query.document_id)

            return DocumentDetail(
                document=document,
                chunks=await uow.documents.list_chunks(
                    document_id, limit=limit, offset=offset
                ),
                chunk_count=await uow.documents.count_chunks(document_id),
            )


class RetryDocumentIngestion:
    """Puts a document back on the ingestion queue.

    The reason this is worth having rather than "just upload it again": the
    bytes are already in object storage, so a retry costs no upload and keeps
    the document's identity, its place in the list, and its history. After a
    transient failure -- a worker killed mid-parse, an embedding provider
    rejecting a burst -- that is the whole fix.

    Re-running is safe by construction: `index_blocks` deletes the document's
    existing chunks and vectors before writing new ones, so a retry replaces
    rather than accumulates.
    """

    def __init__(
        self,
        uow_factory: AiResourceUowFactory,
        ingestion_queue: DocumentIngestionQueue,
        clock: Clock,
    ) -> None:
        self._uow_factory = uow_factory
        self._ingestion_queue = ingestion_queue
        self._clock = clock

    async def execute(self, command: DocumentActionCommand) -> None:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        knowledge_base_id = UUID(command.knowledge_base_id)
        document_id = UUID(command.document_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id, tenant_id) as uow:
            if MANAGE_DOCUMENT_PERMISSION not in command.permissions:
                raise PermissionDeniedError(MANAGE_DOCUMENT_PERMISSION)

            requester = await build_requester_context(
                uow, tenant_id=tenant_id, user_id=actor_id, permissions=command.permissions
            )
            if requester is None:
                raise KnowledgeBaseNotFoundError(command.knowledge_base_id)

            await load_visible_knowledge_base(
                uow,
                knowledge_base_id=knowledge_base_id,
                requester=requester,
                for_modification=True,
            )

            document = await uow.documents.get_by_id(document_id)
            # The knowledge-base check is not enough on its own: it authorizes
            # *a* knowledge base, and without this a caller could pass a
            # document id belonging to a different one. The repository is
            # RLS-scoped, so a cross-tenant id is already invisible; this
            # closes the cross-knowledge-base case inside one tenant.
            if (
                document is None
                or document.is_deleted
                or document.knowledge_base_id != knowledge_base_id
            ):
                raise DocumentNotFoundError(command.document_id)

            # Back to `processing` so the console shows it moving again rather
            # than sitting on its old failure while a worker is already on it.
            document.mark_processing()
            await uow.documents.save(document)
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=actor_id,
                tenant_id=tenant_id,
                action="ai_resources.document_ingestion_retried",
                resource_type="document",
                resource_id=document_id,
                result="success",
                metadata={"knowledge_base_id": str(knowledge_base_id)},
            )

        # After the commit, for the same reason as the initial upload: a worker
        # that picked this up mid-transaction would read the pre-retry row.
        await self._ingestion_queue.enqueue(
            tenant_id=tenant_id,
            actor_user_id=actor_id,
            document_id=document_id,
            at=now,
        )


class DeleteDocument:
    """Removes a document and everything derived from it.

    Three stores hold pieces of one document, and leaving any of them behind
    has a distinct cost:

    - `document_chunks` rows -- dead weight, and they would reappear in a
      rebuild of the vector index.
    - Qdrant points -- **the one that matters.** An orphaned vector still
      matches a search, so a deleted document keeps answering questions and
      citing a source that no longer exists.
    - The stored bytes -- the tenant asked for their file to be gone.

    Ordering is deliberate: vectors first, because that is the copy a query
    can still reach. If a later step fails the document is already unable to
    answer, which is the direction to fail in. The row is soft-deleted last
    (`audit_logs` references it, and this project keeps the referent).
    """

    def __init__(
        self,
        uow_factory: AiResourceUowFactory,
        object_storage: ObjectStorageClient,
        vector_search: VectorSearchClient,
        clock: Clock,
    ) -> None:
        self._uow_factory = uow_factory
        self._object_storage = object_storage
        self._vector_search = vector_search
        self._clock = clock

    async def execute(self, command: DocumentActionCommand) -> None:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        knowledge_base_id = UUID(command.knowledge_base_id)
        document_id = UUID(command.document_id)

        async with self._uow_factory(actor_id, tenant_id) as uow:
            if MANAGE_DOCUMENT_PERMISSION not in command.permissions:
                raise PermissionDeniedError(MANAGE_DOCUMENT_PERMISSION)

            requester = await build_requester_context(
                uow, tenant_id=tenant_id, user_id=actor_id, permissions=command.permissions
            )
            if requester is None:
                raise KnowledgeBaseNotFoundError(command.knowledge_base_id)

            knowledge_base = await load_visible_knowledge_base(
                uow,
                knowledge_base_id=knowledge_base_id,
                requester=requester,
                for_modification=True,
            )

            document = await uow.documents.get_by_id(document_id)
            if (
                document is None
                or document.is_deleted
                or document.knowledge_base_id != knowledge_base_id
            ):
                raise DocumentNotFoundError(command.document_id)

            # Vectors first -- see the class docstring. The namespace comes
            # from the knowledge-base row the caller was just authorized for,
            # never from the request, so a crafted id cannot reach another
            # tenant's collection.
            await self._vector_search.delete_document(
                namespace=knowledge_base.vector_namespace, document_id=document_id
            )
            await uow.documents.delete_chunks(document_id)

            # Best-effort: the bytes are already unreachable through the API
            # once the row is gone, and failing the whole delete because a
            # storage backend hiccuped would leave the tenant with a document
            # they cannot remove.
            try:
                await self._object_storage.delete(path=document.storage_path)
            except Exception:
                logger.exception(
                    "could not delete stored bytes for document %s at %s -- the "
                    "record is removed but the object remains for a sweep",
                    document_id,
                    document.storage_path,
                )

            document.soft_delete(now=self._clock.now())
            await uow.documents.save(document)
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=actor_id,
                tenant_id=tenant_id,
                action="ai_resources.document_deleted",
                resource_type="document",
                resource_id=document_id,
                result="success",
                metadata={
                    "knowledge_base_id": str(knowledge_base_id),
                    "filename": document.filename,
                },
            )
