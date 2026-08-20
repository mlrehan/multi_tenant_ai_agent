"""Knowledge-base lifecycle and document upload -- docs/16-schema-ai-resources.md.

**The security-critical property in this module:** neither
``vector_namespace`` nor ``storage_path`` appears on any command dataclass.
They are derived server-side from the tenant/resource IDs the caller has
already been authorized for, so there is no code path -- not even a
mistaken one -- by which a client can name the namespace or path its data
lands in, or that a later query reads from. That is what makes the
"vector queries always use server-generated tenant filters" requirement
(Phase 1 §12) structural rather than a convention someone must remember.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from iam_platform.application.ai_resources.authorize import load_visible_knowledge_base
from iam_platform.application.ai_resources.entitlements import (
    guard_knowledge_base_quota,
)
from iam_platform.application.ai_resources.exceptions import (
    KnowledgeBaseNotFoundError,
    PermissionDeniedError,
)
from iam_platform.application.ai_resources.ports import (
    AiResourceUowFactory,
    DocumentIngestionQueue,
    ObjectStorageClient,
    ObjectStoragePathFactory,
    VectorNamespaceFactory,
    VectorSearchClient,
)
from iam_platform.application.ai_resources.requester import build_requester_context
from iam_platform.core.clock import Clock
from iam_platform.domain.ai_resources.entities import (
    Document,
    DocumentStatus,
    KnowledgeBase,
    ResourceVisibility,
)
from iam_platform.domain.ai_resources.policies import (
    VIEW_ALL_KNOWLEDGE_BASES,
    can_access_resource,
    describe_knowledge_base,
)

CREATE_KNOWLEDGE_BASE_PERMISSION = "tenant.knowledge_bases.create"
UPLOAD_DOCUMENT_PERMISSION = "tenant.documents.upload"
QUERY_KNOWLEDGE_BASE_PERMISSION = "tenant.knowledge_bases.query"


@dataclass(frozen=True, slots=True)
class CreateKnowledgeBaseCommand:
    actor_user_id: str
    tenant_id: str
    permissions: frozenset[str]
    name: str
    description: str | None = None
    visibility: str = "tenant"
    department_id: str | None = None
    team_id: str | None = None


class CreateKnowledgeBase:
    def __init__(
        self,
        uow_factory: AiResourceUowFactory,
        namespace_factory: VectorNamespaceFactory,
        clock: Clock,
    ) -> None:
        self._uow_factory = uow_factory
        self._namespace_factory = namespace_factory
        self._clock = clock

    async def execute(self, command: CreateKnowledgeBaseCommand) -> UUID:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id, tenant_id) as uow:
            if CREATE_KNOWLEDGE_BASE_PERMISSION not in command.permissions:
                raise PermissionDeniedError(CREATE_KNOWLEDGE_BASE_PERMISSION)

            # Permission first, then plan. A caller who lacks the permission
            # must be told that, not that they are at a limit -- the two send
            # them to different people for a fix.
            await guard_knowledge_base_quota(
                uow, tenant_id=tenant_id, clock=self._clock
            )

            requester = await build_requester_context(
                uow, tenant_id=tenant_id, user_id=actor_id, permissions=command.permissions
            )
            if requester is None:
                raise PermissionDeniedError(CREATE_KNOWLEDGE_BASE_PERMISSION)

            visibility = ResourceVisibility(command.visibility)
            department_id = UUID(command.department_id) if command.department_id else None
            team_id = UUID(command.team_id) if command.team_id else None
            if visibility == ResourceVisibility.DEPARTMENT and department_id is None:
                raise ValueError("department visibility requires a department_id")
            if visibility == ResourceVisibility.TEAM and team_id is None:
                raise ValueError("team visibility requires a team_id")

            knowledge_base_id = uuid4()
            knowledge_base = KnowledgeBase(
                id=knowledge_base_id,
                tenant_id=tenant_id,
                name=command.name,
                description=command.description,
                owner_membership_id=requester.membership_id,
                visibility=visibility,
                department_id=department_id,
                team_id=team_id,
                vector_namespace=self._namespace_factory.build(
                    tenant_id=tenant_id, knowledge_base_id=knowledge_base_id
                ),
                created_at=now,
                updated_at=now,
            )
            await uow.knowledge_bases.add(knowledge_base)
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=actor_id,
                tenant_id=tenant_id,
                action="ai_resources.knowledge_base_created",
                resource_type="knowledge_base",
                resource_id=knowledge_base.id,
                result="success",
                metadata={"name": command.name, "visibility": command.visibility},
            )
            return knowledge_base.id


@dataclass(frozen=True, slots=True)
class ListKnowledgeBasesQuery:
    actor_user_id: str
    tenant_id: str
    permissions: frozenset[str]


class ListKnowledgeBases:
    def __init__(self, uow_factory: AiResourceUowFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, query: ListKnowledgeBasesQuery) -> list[KnowledgeBase]:
        actor_id = UUID(query.actor_user_id)
        tenant_id = UUID(query.tenant_id)

        async with self._uow_factory(actor_id, tenant_id) as uow:
            requester = await build_requester_context(
                uow, tenant_id=tenant_id, user_id=actor_id, permissions=query.permissions
            )
            if requester is None:
                return []
            return [
                kb
                for kb in await uow.knowledge_bases.list_by_tenant(tenant_id)
                if can_access_resource(
                    resource=describe_knowledge_base(kb),
                    requester=requester,
                    explicit_access_level=None,
                    view_all_permission=VIEW_ALL_KNOWLEDGE_BASES,
                )
            ]


@dataclass(frozen=True, slots=True)
class UploadDocumentCommand:
    actor_user_id: str
    tenant_id: str
    knowledge_base_id: str
    permissions: frozenset[str]
    filename: str
    content_type: str
    size_bytes: int
    checksum: str
    #: The actual file bytes. Present on the command rather than streamed
    #: because the API layer has already read and size-checked them -- a
    #: stream here would let an unbounded body reach storage before anything
    #: could reject it.
    content: bytes


class UploadDocument:
    def __init__(
        self,
        uow_factory: AiResourceUowFactory,
        path_factory: ObjectStoragePathFactory,
        ingestion_queue: DocumentIngestionQueue,
        clock: Clock,
        object_storage: ObjectStorageClient,
    ) -> None:
        self._uow_factory = uow_factory
        self._path_factory = path_factory
        self._ingestion_queue = ingestion_queue
        self._clock = clock
        self._object_storage = object_storage

    async def execute(self, command: UploadDocumentCommand) -> UUID:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        knowledge_base_id = UUID(command.knowledge_base_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id, tenant_id) as uow:
            if UPLOAD_DOCUMENT_PERMISSION not in command.permissions:
                raise PermissionDeniedError(UPLOAD_DOCUMENT_PERMISSION)

            requester = await build_requester_context(
                uow, tenant_id=tenant_id, user_id=actor_id, permissions=command.permissions
            )
            if requester is None:
                raise KnowledgeBaseNotFoundError(command.knowledge_base_id)

            # Uploading changes the knowledge base's contents, so it needs
            # modify rights on it -- read access is not enough.
            await load_visible_knowledge_base(
                uow,
                knowledge_base_id=knowledge_base_id,
                requester=requester,
                for_modification=True,
            )

            document_id = uuid4()
            storage_path = self._path_factory.build(
                tenant_id=tenant_id,
                knowledge_base_id=knowledge_base_id,
                document_id=document_id,
            )

            # Bytes first, row second. If the write fails, no row is created
            # and the upload reports failure. If the row insert fails instead,
            # the bytes are orphaned -- garbage a purge can sweep, and far
            # better than the reverse: a row pointing at content that was
            # never stored would make the document permanently unreadable
            # while looking perfectly healthy in the console.
            await self._object_storage.put(
                path=storage_path,
                data=command.content,
                content_type=command.content_type,
            )

            document = Document(
                id=document_id,
                tenant_id=tenant_id,
                knowledge_base_id=knowledge_base_id,
                uploaded_by_membership_id=requester.membership_id,
                filename=command.filename,
                content_type=command.content_type,
                storage_path=storage_path,
                size_bytes=command.size_bytes,
                status=DocumentStatus.PROCESSING,
                checksum=command.checksum,
                created_at=now,
            )
            await uow.documents.add(document)
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=actor_id,
                tenant_id=tenant_id,
                action="ai_resources.document_uploaded",
                resource_type="document",
                resource_id=document.id,
                result="success",
                metadata={
                    "knowledge_base_id": str(knowledge_base_id),
                    "filename": command.filename,
                    "size_bytes": command.size_bytes,
                },
            )

        # Enqueued only after the transaction commits -- a worker that picked
        # the job up mid-transaction would find no document row.
        await self._ingestion_queue.enqueue(
            tenant_id=tenant_id,
            actor_user_id=actor_id,
            document_id=document_id,
            at=now,
        )
        return document_id


@dataclass(frozen=True, slots=True)
class QueryKnowledgeBaseQuery:
    actor_user_id: str
    tenant_id: str
    knowledge_base_id: str
    permissions: frozenset[str]
    query_text: str
    top_k: int = 10


@dataclass(frozen=True, slots=True)
class KnowledgeBaseSearchHit:
    document_id: UUID
    filename: str
    score: float


class QueryKnowledgeBase:
    """Retrieval against one knowledge base's vector namespace.

    The namespace passed to the search client is read off the stored
    ``KnowledgeBase`` row the caller was just authorized for -- never taken
    from the request. Combined with ``vector_namespace`` being server-derived
    at creation, a caller cannot reach another tenant's (or another knowledge
    base's) vectors even if they guess IDs, because an unauthorized
    ``knowledge_base_id`` fails the visibility check before any search runs.
    """

    def __init__(
        self, uow_factory: AiResourceUowFactory, search_client: VectorSearchClient
    ) -> None:
        self._uow_factory = uow_factory
        self._search_client = search_client

    async def execute(self, query: QueryKnowledgeBaseQuery) -> list[KnowledgeBaseSearchHit]:
        actor_id = UUID(query.actor_user_id)
        tenant_id = UUID(query.tenant_id)
        knowledge_base_id = UUID(query.knowledge_base_id)

        async with self._uow_factory(actor_id, tenant_id) as uow:
            if QUERY_KNOWLEDGE_BASE_PERMISSION not in query.permissions:
                raise PermissionDeniedError(QUERY_KNOWLEDGE_BASE_PERMISSION)

            requester = await build_requester_context(
                uow, tenant_id=tenant_id, user_id=actor_id, permissions=query.permissions
            )
            if requester is None:
                raise KnowledgeBaseNotFoundError(query.knowledge_base_id)

            knowledge_base = await load_visible_knowledge_base(
                uow, knowledge_base_id=knowledge_base_id, requester=requester
            )

            raw_hits = await self._search_client.query(
                namespace=knowledge_base.vector_namespace,
                query_text=query.query_text,
                top_k=query.top_k,
            )

            # Re-read each hit through the RLS-scoped repository rather than
            # trusting the vector store's metadata. If the two ever disagree
            # -- stale embedding, deleted document, a namespace collision --
            # the database wins and the row is dropped from the results.
            documents = {d.id: d for d in await uow.documents.list_by_knowledge_base(knowledge_base_id)}
            hits: list[KnowledgeBaseSearchHit] = []
            for document_id, score in raw_hits:
                document = documents.get(document_id)
                if document is None or document.is_deleted:
                    continue
                hits.append(
                    KnowledgeBaseSearchHit(
                        document_id=document.id, filename=document.filename, score=score
                    )
                )
            return hits


@dataclass(frozen=True, slots=True)
class ListDocumentsQuery:
    actor_user_id: str
    tenant_id: str
    knowledge_base_id: str
    permissions: frozenset[str]


@dataclass(frozen=True, slots=True)
class DocumentSummary:
    """A document plus the one fact its own row cannot tell you.

    `status = 'ready'` says the pipeline finished. `chunk_count` says it
    produced something searchable, and the two can disagree: a scanned PDF
    whose OCR failed used to arrive here as `ready` with nothing indexed, which
    looks like success and answers no question. Surfacing the count is what
    makes that visible in the console rather than only in a chunk table nobody
    reads.

    Derived, so it belongs on a read model rather than on `Document` -- the
    entity describes the upload, not the state of a downstream index.
    """

    document: Document
    chunk_count: int


class ListDocuments:
    """Documents in one knowledge base, with their ingestion status.

    Gated by the same visibility check as reading the knowledge base itself
    (not the *upload* permission): being able to see a knowledge base means
    being able to see what is in it. Soft-deleted rows are excluded by the
    repository.
    """

    def __init__(self, uow_factory: AiResourceUowFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, query: ListDocumentsQuery) -> list[DocumentSummary]:
        actor_id = UUID(query.actor_user_id)
        tenant_id = UUID(query.tenant_id)

        async with self._uow_factory(actor_id, tenant_id) as uow:
            requester = await build_requester_context(
                uow, tenant_id=tenant_id, user_id=actor_id, permissions=query.permissions
            )
            if requester is None:
                raise KnowledgeBaseNotFoundError(query.knowledge_base_id)

            # Raises KnowledgeBaseNotFoundError if invisible -- so a caller
            # cannot use this endpoint to prove a knowledge base exists.
            await load_visible_knowledge_base(
                uow,
                knowledge_base_id=UUID(query.knowledge_base_id),
                requester=requester,
            )
            documents = await uow.documents.list_by_knowledge_base(
                UUID(query.knowledge_base_id)
            )
            # One count per document. A single grouped query would be fewer
            # round trips, but a knowledge base holds tens to hundreds of
            # documents, not millions, and adding a bespoke aggregate to the
            # repository for that is optimising the wrong thing first.
            return [
                DocumentSummary(
                    document=document,
                    chunk_count=await uow.documents.count_chunks(document.id),
                )
                for document in documents
            ]
