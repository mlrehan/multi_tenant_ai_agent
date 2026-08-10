"""Re-syncing an existing crawl.

The property that matters most here is **what re-sync does not do**: it must
not delete documents for pages that have disappeared from the site. A site
that 404s briefly during a deploy would otherwise quietly empty a tenant's
knowledge base, and that is a decision no automatic refresh should make.

The second is that the stored URLs are re-validated. Passing the SSRF guard
once, at creation, says nothing about where a hostname resolves today -- the
whole point of resolving-then-checking is that it happens at the moment of
use.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from iam_platform.application.ai_resources.exceptions import (
    DataSourceNotFoundError,
    PermissionDeniedError,
)
from iam_platform.application.ai_resources.manage_data_source import (
    ResyncDataSource,
    ResyncDataSourceCommand,
)
from iam_platform.core.clock import FixedClock
from iam_platform.domain.ai_resources.entities import (
    CrawlMode,
    DataSource,
    DataSourceKind,
    KnowledgeBase,
    ResourceVisibility,
    SyncStatus,
)
from iam_platform.domain.tenancy.entities import MembershipStatus, TenantMembership
from iam_platform.infrastructure.crawling.url_safety import UnsafeCrawlTargetError
from tests.unit.ai_resources.fakes import (
    FakeAiResourceUnitOfWork,
    FakeCrawlJobQueue,
    FakeUrlValidator,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)
LATER = datetime(2026, 2, 1, tzinfo=UTC)
UPLOAD = "tenant.documents.upload"


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


def _seed_source(
    uow: FakeAiResourceUnitOfWork,
    kb: KnowledgeBase,
    membership_id,
    *,
    urls: list[str] | None = None,
) -> DataSource:
    source = DataSource(
        id=uuid4(),
        tenant_id=kb.tenant_id,
        knowledge_base_id=kb.id,
        kind=DataSourceKind.URL_CRAWL,
        urls=urls or ["https://example.test/docs"],
        mode=CrawlMode.URL_LIST,
        created_by_membership_id=membership_id,
        sync_status=SyncStatus.READY,
        pages_discovered=12,
        pages_indexed=11,
        last_synced_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )
    uow.data_sources.by_id[source.id] = source
    return source


def _command(user_id, tenant_id, kb_id, source_id, *, permissions=frozenset({UPLOAD})):
    return ResyncDataSourceCommand(
        actor_user_id=str(user_id),
        tenant_id=str(tenant_id),
        knowledge_base_id=str(kb_id),
        data_source_id=str(source_id),
        permissions=permissions,
    )


class TestResyncDataSource:
    async def test_resync_resets_progress_and_re_enqueues_the_same_job(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, membership = _seed_member(uow, tenant_id)
        kb = _seed_knowledge_base(uow, tenant_id, membership.id)
        source = _seed_source(uow, kb, membership.id)
        queue = FakeCrawlJobQueue()

        await ResyncDataSource(uow, queue, FakeUrlValidator(), FixedClock(LATER)).execute(
            _command(user_id, tenant_id, kb.id, source.id)
        )

        stored = await uow.data_sources.get(tenant_id=tenant_id, source_id=source.id)
        assert stored is not None
        assert stored.sync_status is SyncStatus.SYNCING
        # Reset, so the console shows this run's progress rather than the
        # previous run's totals while the crawl is still going.
        assert (stored.pages_discovered, stored.pages_indexed) == (0, 0)
        assert queue.enqueued == [(tenant_id, source.id)]
        assert queue.enqueued_actors == [user_id]

    async def test_resync_does_not_delete_previously_indexed_documents(self) -> None:
        """A refresh must not empty a knowledge base because a site is briefly
        down. The crawl job updates pages it finds again; anything it no longer
        finds stays until someone deletes it deliberately."""
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, membership = _seed_member(uow, tenant_id)
        kb = _seed_knowledge_base(uow, tenant_id, membership.id)
        source = _seed_source(uow, kb, membership.id)
        queue = FakeCrawlJobQueue()

        before = dict(uow.documents.by_id)
        await ResyncDataSource(uow, queue, FakeUrlValidator(), FixedClock(LATER)).execute(
            _command(user_id, tenant_id, kb.id, source.id)
        )

        assert uow.documents.by_id == before

    async def test_stored_urls_are_revalidated_before_the_job_is_queued(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, membership = _seed_member(uow, tenant_id)
        kb = _seed_knowledge_base(uow, tenant_id, membership.id)
        source = _seed_source(
            uow, kb, membership.id, urls=["https://a.test/x", "https://b.test/y"]
        )
        queue = FakeCrawlJobQueue()
        validator = FakeUrlValidator()

        await ResyncDataSource(uow, queue, validator, FixedClock(LATER)).execute(
            _command(user_id, tenant_id, kb.id, source.id)
        )

        assert validator.checked == ["https://a.test/x", "https://b.test/y"]

    async def test_a_url_that_became_unsafe_refuses_the_resync(self) -> None:
        """The address was fine when the source was created; it resolves
        somewhere internal now. Nothing is queued and the status is untouched."""
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, membership = _seed_member(uow, tenant_id)
        kb = _seed_knowledge_base(uow, tenant_id, membership.id)
        source = _seed_source(uow, kb, membership.id, urls=["https://gone-bad.test/x"])
        queue = FakeCrawlJobQueue()

        with pytest.raises(UnsafeCrawlTargetError):
            await ResyncDataSource(
                uow,
                queue,
                FakeUrlValidator(unsafe={"https://gone-bad.test/x"}),
                FixedClock(LATER),
            ).execute(_command(user_id, tenant_id, kb.id, source.id))

        assert queue.enqueued == []
        stored = await uow.data_sources.get(tenant_id=tenant_id, source_id=source.id)
        assert stored is not None
        assert stored.sync_status is SyncStatus.READY

    async def test_resync_without_the_permission_is_refused(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, membership = _seed_member(uow, tenant_id)
        kb = _seed_knowledge_base(uow, tenant_id, membership.id)
        source = _seed_source(uow, kb, membership.id)
        queue = FakeCrawlJobQueue()

        with pytest.raises(PermissionDeniedError):
            await ResyncDataSource(
                uow, queue, FakeUrlValidator(), FixedClock(LATER)
            ).execute(
                _command(user_id, tenant_id, kb.id, source.id, permissions=frozenset())
            )

        assert queue.enqueued == []

    async def test_a_source_in_another_knowledge_base_is_not_found(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id = uuid4()
        user_id, membership = _seed_member(uow, tenant_id)
        authorized_kb = _seed_knowledge_base(uow, tenant_id, membership.id)
        other_kb = _seed_knowledge_base(uow, tenant_id, membership.id)
        source = _seed_source(uow, other_kb, membership.id)
        queue = FakeCrawlJobQueue()

        with pytest.raises(DataSourceNotFoundError):
            await ResyncDataSource(
                uow, queue, FakeUrlValidator(), FixedClock(LATER)
            ).execute(_command(user_id, tenant_id, authorized_kb.id, source.id))

        assert queue.enqueued == []
        stored = await uow.data_sources.get(tenant_id=tenant_id, source_id=source.id)
        assert stored is not None
        assert stored.sync_status is SyncStatus.READY
