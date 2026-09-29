"""Deleting a knowledge base.

What must hold: every document in it leaves every store (vectors above all --
an orphaned vector keeps answering questions), the knowledge base then does
not exist for any caller, the deletion is audited, and it is refused -- with
nothing touched -- while a chatbot still answers from it or anything is still
being ingested. Only its owner or a holder of `tenant.knowledge_bases.manage`
may do it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from iam_platform.application.ai_resources.delete_knowledge_base import (
    DeleteKnowledgeBase,
    DeleteKnowledgeBaseCommand,
)
from iam_platform.application.ai_resources.exceptions import (
    KnowledgeBaseInUseError,
    KnowledgeBaseNotFoundError,
    ResourceAccessDeniedError,
)
from iam_platform.core.clock import FixedClock
from iam_platform.domain.ai_resources.entities import (
    ChatWidget,
    CrawlMode,
    DataSource,
    DataSourceKind,
    DocumentStatus,
    KnowledgeBase,
    SyncStatus,
)
from tests.unit.ai_resources.fakes import (
    FakeAiResourceUnitOfWork,
    FakeObjectStorageClient,
    FakeVectorSearchClient,
)
from tests.unit.ai_resources.test_manage_document import (
    _seed_document,
    _seed_knowledge_base,
    _seed_member,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 29, tzinfo=UTC)


class _World:
    def __init__(self) -> None:
        self.uow = FakeAiResourceUnitOfWork()
        self.tenant_id = uuid4()
        self.user_id, self.membership = _seed_member(self.uow, self.tenant_id)
        self.kb = _seed_knowledge_base(self.uow, self.tenant_id, self.membership.id)
        self.vectors = FakeVectorSearchClient()
        self.storage = FakeObjectStorageClient()

    def document(self, **kwargs: object):  # type: ignore[no-untyped-def]
        doc = _seed_document(self.uow, self.kb, status=DocumentStatus.READY, failure_reason=None, **kwargs)
        self.storage.objects[doc.storage_path] = (b"bytes", "application/pdf")
        return doc

    async def delete(self, *, user_id: UUID | None = None, permissions: frozenset[str] = frozenset()) -> None:
        await DeleteKnowledgeBase(
            self.uow,  # type: ignore[arg-type]
            self.storage,
            self.vectors,  # type: ignore[arg-type]
            FixedClock(NOW),
        ).execute(
            DeleteKnowledgeBaseCommand(
                actor_user_id=str(user_id or self.user_id),
                tenant_id=str(self.tenant_id),
                knowledge_base_id=str(self.kb.id),
                permissions=permissions,
            )
        )


def _widget(kb: KnowledgeBase) -> ChatWidget:
    return ChatWidget(
        id=uuid4(),
        tenant_id=kb.tenant_id,
        knowledge_base_id=kb.id,
        name="Website chatbot",
        public_key="wk_test",
        allowed_origins=["https://nursery.example"],
        created_by_membership_id=kb.owner_membership_id,
        created_at=NOW,
        updated_at=NOW,
    )


def _source(kb: KnowledgeBase, status: SyncStatus) -> DataSource:
    return DataSource(
        id=uuid4(),
        tenant_id=kb.tenant_id,
        knowledge_base_id=kb.id,
        kind=DataSourceKind.URL_CRAWL,
        urls=["https://nursery.example"],
        mode=CrawlMode.URL_LIST,
        created_by_membership_id=kb.owner_membership_id,
        sync_status=status,
        created_at=NOW,
        updated_at=NOW,
    )


class TestDeletingRemovesEverythingSearchable:
    async def test_every_document_leaves_every_store(self) -> None:
        world = _World()
        docs = [world.document(chunks=3), world.document(chunks=5)]

        await world.delete()

        # Vectors, in this knowledge base's own namespace.
        assert set(world.vectors.deleted) == {(world.kb.vector_namespace, d.id) for d in docs}
        for d in docs:
            assert world.uow.documents.chunks.get(d.id, 0) == 0
            assert d.storage_path not in world.storage.objects
            assert world.uow.documents.by_id[d.id].is_deleted

    async def test_the_knowledge_base_then_does_not_exist(self) -> None:
        world = _World()
        await world.delete()
        assert await world.uow.knowledge_bases.get_by_id(world.kb.id) is None
        assert world.kb.id not in [
            k.id for k in await world.uow.knowledge_bases.list_by_tenant(world.tenant_id)
        ]
        with pytest.raises(KnowledgeBaseNotFoundError):
            await world.delete()  # a second delete finds nothing

    async def test_it_is_audited_with_what_was_removed(self) -> None:
        world = _World()
        world.document(chunks=3)
        world.document(chunks=5)
        await world.delete()
        (event,) = [e for e in world.uow.audit.events if e["action"] == "ai_resources.knowledge_base_deleted"]
        assert event["resource_id"] == world.kb.id
        assert event["metadata"]["documents_removed"] == 2
        assert event["metadata"]["passages_removed"] == 8


class TestRefusedWhileInUse:
    async def test_a_chatbot_still_using_it_blocks_the_delete(self) -> None:
        world = _World()
        doc = world.document(chunks=3)
        widget = _widget(world.kb)
        world.uow.chat_widgets.by_id[widget.id] = widget

        with pytest.raises(KnowledgeBaseInUseError, match="Website chatbot"):
            await world.delete()
        assert world.vectors.deleted == []
        assert not world.uow.documents.by_id[doc.id].is_deleted
        assert await world.uow.knowledge_bases.get_by_id(world.kb.id) is not None

    async def test_a_document_still_processing_blocks_the_delete(self) -> None:
        world = _World()
        doc = world.document()
        doc.status = DocumentStatus.PROCESSING
        with pytest.raises(KnowledgeBaseInUseError, match="being processed"):
            await world.delete()
        assert world.vectors.deleted == []

    async def test_a_crawl_in_progress_blocks_the_delete(self) -> None:
        world = _World()
        source = _source(world.kb, SyncStatus.SYNCING)
        world.uow.data_sources.by_id[source.id] = source
        with pytest.raises(KnowledgeBaseInUseError, match="being processed"):
            await world.delete()
        assert await world.uow.knowledge_bases.get_by_id(world.kb.id) is not None

    async def test_a_finished_crawl_does_not_block_it(self) -> None:
        world = _World()
        source = _source(world.kb, SyncStatus.READY)
        world.uow.data_sources.by_id[source.id] = source
        await world.delete()
        assert await world.uow.knowledge_bases.get_by_id(world.kb.id) is None


class TestOnlyItsOwnerOrAManagerMayDeleteIt:
    async def test_another_member_without_manage_is_refused(self) -> None:
        world = _World()
        doc = world.document(chunks=2)
        other_user, _ = _seed_member(world.uow, world.tenant_id)
        with pytest.raises(ResourceAccessDeniedError):
            await world.delete(user_id=other_user)
        assert world.vectors.deleted == []
        assert not world.uow.documents.by_id[doc.id].is_deleted

    async def test_a_member_with_manage_may(self) -> None:
        world = _World()
        other_user, _ = _seed_member(world.uow, world.tenant_id)
        await world.delete(user_id=other_user, permissions=frozenset({"tenant.knowledge_bases.manage"}))
        assert await world.uow.knowledge_bases.get_by_id(world.kb.id) is None
