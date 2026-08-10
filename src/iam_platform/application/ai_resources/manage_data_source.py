"""URL and website ingestion sources -- Phase 12.

Goes through the *same* authorization path as document upload:
``build_requester_context`` then ``load_visible_knowledge_base(...,
for_modification=True)``. Adding a crawl changes what a knowledge base
contains, so it needs modify rights on it -- read access is not enough, and a
separate "crawl permission" would be a second answer to a question
``tenant.documents.upload`` already answers.

**URLs are validated at this boundary *and* re-validated inside the crawl
loop.** Not redundancy: they answer different questions. Here, a tenant who
submits an internal address gets an immediate, actionable 400 rather than a job
that fails silently minutes later. There, links *discovered mid-crawl* are
checked -- and nothing at this boundary can see those.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from iam_platform.application.ai_resources.authorize import load_visible_knowledge_base
from iam_platform.application.ai_resources.exceptions import (
    DataSourceNotFoundError,
    KnowledgeBaseNotFoundError,
    PermissionDeniedError,
    TooManyUrlsError,
)
from iam_platform.application.ai_resources.ports import (
    AiResourceUowFactory,
    CrawlJobQueue,
    UrlValidator,
)
from iam_platform.application.ai_resources.requester import build_requester_context
from iam_platform.core.clock import Clock
from iam_platform.domain.ai_resources.entities import (
    CrawlMode,
    DataSource,
    DataSourceKind,
)

#: Deliberately the *upload* permission. A crawl adds documents to a knowledge
#: base; that is the same authority, arriving by a different route.
CREATE_DATA_SOURCE_PERMISSION = "tenant.documents.upload"

#: A per-source cap, distinct from the crawler's page budget. Someone pasting
#: two hundred URLs into one source has almost certainly pasted a file by
#: mistake, and each URL is a separate fetch this platform pays for.
MAX_URLS_PER_SOURCE = 50


@dataclass(frozen=True, slots=True)
class CreateDataSourceCommand:
    actor_user_id: str
    tenant_id: str
    knowledge_base_id: str
    permissions: frozenset[str]
    urls: list[str]
    mode: str


class CreateDataSource:
    def __init__(
        self,
        uow_factory: AiResourceUowFactory,
        crawl_queue: CrawlJobQueue,
        url_validator: UrlValidator,
        clock: Clock,
    ) -> None:
        self._uow_factory = uow_factory
        self._crawl_queue = crawl_queue
        self._url_validator = url_validator
        self._clock = clock

    async def execute(self, command: CreateDataSourceCommand) -> UUID:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        knowledge_base_id = UUID(command.knowledge_base_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id, tenant_id) as uow:
            if CREATE_DATA_SOURCE_PERMISSION not in command.permissions:
                raise PermissionDeniedError(CREATE_DATA_SOURCE_PERMISSION)

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

            if len(command.urls) > MAX_URLS_PER_SOURCE:
                raise TooManyUrlsError(
                    f"a data source accepts at most {MAX_URLS_PER_SOURCE} URLs, "
                    f"got {len(command.urls)}"
                )

            # Before anything is persisted: a refused URL must not leave a
            # source row behind that a tenant then has to clean up.
            for url in command.urls:
                self._url_validator.assert_safe(url)

            source = DataSource(
                id=uuid4(),
                tenant_id=tenant_id,
                knowledge_base_id=knowledge_base_id,
                kind=DataSourceKind.URL_CRAWL,
                urls=list(command.urls),
                mode=CrawlMode(command.mode),
                created_by_membership_id=requester.membership_id,
                created_at=now,
                updated_at=now,
            )
            await uow.data_sources.add(source)
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=actor_id,
                tenant_id=tenant_id,
                action="ai_resources.data_source_created",
                resource_type="data_source",
                resource_id=source.id,
                result="success",
                metadata={
                    "knowledge_base_id": str(knowledge_base_id),
                    "mode": command.mode,
                    # The URLs themselves, not just a count: "who pointed this
                    # platform at what, and when" is exactly the question an
                    # incident review asks about a crawler.
                    "urls": list(command.urls),
                },
            )

        # Enqueued *after* the transaction commits. Inside it, a worker fast
        # enough to pick the job up before the commit lands would look for a
        # row that does not exist yet -- a race that is rare, real, and
        # miserable to diagnose.
        await self._crawl_queue.enqueue(
            tenant_id=tenant_id,
            actor_user_id=actor_id,
            data_source_id=source.id,
            at=now,
        )
        return source.id


@dataclass(frozen=True, slots=True)
class ResyncDataSourceCommand:
    actor_user_id: str
    tenant_id: str
    knowledge_base_id: str
    data_source_id: str
    permissions: frozenset[str]


class ResyncDataSource:
    """Re-runs an existing crawl.

    **No second pipeline.** This re-enqueues the *same* job `CreateDataSource`
    enqueues, and that job is already idempotent by construction: a crawled
    page is looked up by `(knowledge_base_id, source_url)` and updated rather
    than inserted again (backed by `uq_documents_source_url_per_kb`), and
    `index_blocks` deletes a document's chunks and vectors before writing new
    ones. So a re-sync refreshes pages that changed, adds pages that appeared,
    and does not accumulate duplicates.

    What it deliberately does **not** do is remove documents for pages that
    have since vanished from the site. Deleting a tenant's indexed content as
    a side effect of a refresh is not a decision this use case should make
    silently -- a site that briefly 404s during a deploy would otherwise
    quietly empty a knowledge base. Those documents stay, and can be deleted
    individually.

    **The stored URLs are re-validated, not trusted because they passed once.**
    A hostname that resolved to a public address when the source was created
    can resolve somewhere else entirely by the time someone presses re-sync;
    the whole point of the SSRF guard is that resolution is checked at the
    moment of use.
    """

    def __init__(
        self,
        uow_factory: AiResourceUowFactory,
        crawl_queue: CrawlJobQueue,
        url_validator: UrlValidator,
        clock: Clock,
    ) -> None:
        self._uow_factory = uow_factory
        self._crawl_queue = crawl_queue
        self._url_validator = url_validator
        self._clock = clock

    async def execute(self, command: ResyncDataSourceCommand) -> None:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        knowledge_base_id = UUID(command.knowledge_base_id)
        data_source_id = UUID(command.data_source_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id, tenant_id) as uow:
            if CREATE_DATA_SOURCE_PERMISSION not in command.permissions:
                raise PermissionDeniedError(CREATE_DATA_SOURCE_PERMISSION)

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

            source = await uow.data_sources.get(
                tenant_id=tenant_id, source_id=data_source_id
            )
            # The knowledge-base check authorizes *a* knowledge base; without
            # this a caller could pass the id of a source belonging to a
            # different one. RLS already hides another tenant's rows, so this
            # closes the cross-knowledge-base case inside one tenant.
            if source is None or source.knowledge_base_id != knowledge_base_id:
                raise DataSourceNotFoundError(command.data_source_id)

            for url in source.urls:
                self._url_validator.assert_safe(url)

            # Resets the page counters as well as the status, so the console
            # shows this run's progress rather than the previous run's totals
            # while the crawl is still going.
            source.mark_syncing()
            await uow.data_sources.save(source)
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=actor_id,
                tenant_id=tenant_id,
                action="ai_resources.data_source_resynced",
                resource_type="data_source",
                resource_id=source.id,
                result="success",
                metadata={
                    "knowledge_base_id": str(knowledge_base_id),
                    "urls": list(source.urls),
                },
            )

        # After the commit, for the same reason as creation: a worker quick
        # enough to claim the job first would read the pre-reset row.
        await self._crawl_queue.enqueue(
            tenant_id=tenant_id,
            actor_user_id=actor_id,
            data_source_id=source.id,
            at=now,
        )


@dataclass(frozen=True, slots=True)
class ListDataSourcesQuery:
    actor_user_id: str
    tenant_id: str
    knowledge_base_id: str
    permissions: frozenset[str]


class ListDataSources:
    def __init__(self, uow_factory: AiResourceUowFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, query: ListDataSourcesQuery) -> list[DataSource]:
        actor_id = UUID(query.actor_user_id)
        tenant_id = UUID(query.tenant_id)
        knowledge_base_id = UUID(query.knowledge_base_id)

        async with self._uow_factory(actor_id, tenant_id) as uow:
            requester = await build_requester_context(
                uow, tenant_id=tenant_id, user_id=actor_id, permissions=query.permissions
            )
            if requester is None:
                raise KnowledgeBaseNotFoundError(query.knowledge_base_id)

            # Reading the sources requires only being able to see the
            # knowledge base -- `for_modification=False`. Someone who can read
            # a knowledge base can already read everything the crawl indexed
            # into it, so hiding the list of URLs from them would protect
            # nothing.
            await load_visible_knowledge_base(
                uow,
                knowledge_base_id=knowledge_base_id,
                requester=requester,
                for_modification=False,
            )
            return await uow.data_sources.list_for_knowledge_base(
                tenant_id=tenant_id, knowledge_base_id=knowledge_base_id
            )
