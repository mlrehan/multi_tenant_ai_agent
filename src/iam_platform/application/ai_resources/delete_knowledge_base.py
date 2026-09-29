# --------------------------------------------------------------
# src/iam_platform/application/ai_resources/delete_knowledge_base.py
# --------------------------------------------------------------

"""Deleting a knowledge base.

**A soft delete, with everything searchable removed first.** A knowledge base
is referenced by its documents, data sources, chat widgets and answer feedback
(append-only history), so the row is kept and marked; the repository then
treats it as not existing. What a question could still reach -- vectors,
passages, stored files -- is removed document by document through
`purge_document`, the same helper a single-document delete uses, so the two
cannot drift.

**Refused (409) rather than forced in two cases**, each with a message saying
what to do instead:

* a chatbot still answers from it. Deleting the source out from under a live
  widget would leave every visitor question failing with "not found" -- the
  tenant should delete the chatbot or point it elsewhere first;
* something in it is still being ingested, or a crawl is running. The worker
  would otherwise go on writing into a knowledge base that no longer exists.

Authorized like modifying the knowledge base: its owner, or a holder of
`tenant.knowledge_bases.manage`. A knowledge base the caller cannot see is
reported as not found, never as forbidden.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from iam_platform.application.ai_resources.authorize import load_visible_knowledge_base
from iam_platform.application.ai_resources.exceptions import (
    KnowledgeBaseInUseError,
    KnowledgeBaseNotFoundError,
)
from iam_platform.application.ai_resources.manage_document import purge_document
from iam_platform.application.ai_resources.ports import (
    AiResourceUowFactory,
    ObjectStorageClient,
    VectorSearchClient,
)
from iam_platform.application.ai_resources.requester import build_requester_context
from iam_platform.core.clock import Clock
from iam_platform.domain.ai_resources.entities import DocumentStatus, SyncStatus


@dataclass(frozen=True, slots=True)
class DeleteKnowledgeBaseCommand:
    actor_user_id: str
    tenant_id: str
    knowledge_base_id: str
    permissions: frozenset[str]


class DeleteKnowledgeBase:
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

    async def execute(self, command: DeleteKnowledgeBaseCommand) -> None:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        knowledge_base_id = UUID(command.knowledge_base_id)

        async with self._uow_factory(actor_id, tenant_id) as uow:
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

            widgets = [
                w
                for w in await uow.chat_widgets.list_for_tenant(tenant_id)
                if w.knowledge_base_id == knowledge_base_id
            ]
            if widgets:
                names = ", ".join(f"“{w.name}”" for w in widgets)
                raise KnowledgeBaseInUseError(
                    f"{knowledge_base.name} is used by your chatbot {names}. Delete that "
                    "chatbot, or connect it to another knowledge base, before deleting this one."
                )

            documents = await uow.documents.list_by_knowledge_base(knowledge_base_id)
            sources = await uow.data_sources.list_for_knowledge_base(
                tenant_id=tenant_id, knowledge_base_id=knowledge_base_id
            )
            if any(d.status is DocumentStatus.PROCESSING for d in documents) or any(
                s.sync_status is SyncStatus.SYNCING for s in sources
            ):
                raise KnowledgeBaseInUseError(
                    f"{knowledge_base.name} still has files or web pages being processed. "
                    "Wait until they finish, then delete it."
                )

            now = self._clock.now()
            removed = 0
            chunks = 0
            for document in documents:
                if document.is_deleted:
                    continue
                removed += 1
                chunks += await uow.documents.count_chunks(document.id)
                await purge_document(
                    uow,
                    document=document,
                    namespace=knowledge_base.vector_namespace,
                    object_storage=self._object_storage,
                    vector_search=self._vector_search,
                    now=now,
                )

            knowledge_base.soft_delete(now=now)
            await uow.knowledge_bases.save(knowledge_base)
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=actor_id,
                tenant_id=tenant_id,
                action="ai_resources.knowledge_base_deleted",
                resource_type="knowledge_base",
                resource_id=knowledge_base_id,
                result="success",
                metadata={
                    "name": knowledge_base.name,
                    "documents_removed": removed,
                    "passages_removed": chunks,
                    "web_sources": len(sources),
                },
            )
