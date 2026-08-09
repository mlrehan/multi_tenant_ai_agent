"""Crawls a data source and indexes every page it finds.

**A crawled page is a `documents` row like any other.** Not a parallel concept
with its own storage, its own status vocabulary and its own retrieval path --
the same table, the same chunk rows, the same vector namespace, so a knowledge
base holding both uploads and crawled pages answers one query across both. The
only difference is provenance: `data_source_id` and `source_url` are set, and
`storage_path` holds the fetched markdown rather than an uploaded file.

**The markdown is stored, not just indexed.** It costs a few kilobytes and it
means a re-embed (a model change, a chunk-size change) does not require
re-crawling the site -- which would be slow, would hammer someone else's
server, and might return different content than was originally indexed.

**Per-page commits, not one transaction for the whole crawl.** A 500-page
crawl in a single transaction holds locks for its entire runtime, and a failure
on page 499 throws away 498 pages of paid-for embedding work. Each page is its
own unit: interrupted at page 400, a crawl has indexed 400 pages.

**Failure is per-page and per-source.** A page that will not parse marks
nothing failed -- it is skipped and counted. Only something that stops the
whole crawl (an unreachable start URL, a refused target) marks the *source*
`error`, with a reason the tenant can act on. Same separation of concerns as
`process_document_upload`: a bad page is not a broken job.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from iam_platform.application.ai_resources.ports import (
    CrawledPage,
    CrawlLimits,
    CrawlMode,
    EmbeddingClient,
    ObjectStorageClient,
    ParsedBlock,
    VectorSearchClient,
    WebCrawler,
)
from iam_platform.infrastructure.crawling.url_safety import UnsafeCrawlTargetError
from iam_platform.infrastructure.parsing.chunking import TokenAwareChunker
from iam_platform.workers.job_context import (
    JobAuthorizationError,
    VerifiedJobContext,
    establish_job_context,
)
from iam_platform.workers.jobs.indexing import IndexingTarget, index_blocks

logger = logging.getLogger("iam_platform.workers.jobs.process_url_crawl")

_CRAWLED_CONTENT_TYPE = "text/markdown"


@dataclass(frozen=True, slots=True)
class _DataSourceRow:
    knowledge_base_id: UUID
    vector_namespace: str
    storage_prefix: str
    created_by_membership_id: UUID
    urls: list[str]
    mode: CrawlMode


@dataclass(frozen=True, slots=True)
class CrawlDependencies:
    crawler: WebCrawler
    object_storage: ObjectStorageClient
    chunker: TokenAwareChunker
    embedding_client: EmbeddingClient
    vector_search: VectorSearchClient
    limits: CrawlLimits


class CrawlFailed(Exception):
    """Wraps whatever stopped the crawl, with a message fit to show a tenant."""


async def process_url_crawl(
    session_factory: Callable[[], AsyncSession],
    dependencies: CrawlDependencies,
    *,
    tenant_id: UUID,
    actor_user_id: UUID,
    data_source_id: UUID,
) -> None:
    """Entry point. Never raises for a crawl failure -- it records one.

    Re-raises only ``JobAuthorizationError``: that is not a data-source
    problem, the job should be dropped, and the RLS context needed to write the
    error is exactly what was refused.
    """
    try:
        source = await _claim(
            session_factory,
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            data_source_id=data_source_id,
        )
        discovered, indexed = await _crawl_and_index(
            session_factory,
            dependencies,
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            data_source_id=data_source_id,
            source=source,
        )
        await _finish(
            session_factory,
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            data_source_id=data_source_id,
            discovered=discovered,
            indexed=indexed,
        )
        logger.info(
            "crawl %s indexed %s of %s pages", data_source_id, indexed, discovered
        )
    except JobAuthorizationError:
        logger.warning(
            "refusing crawl %s: job authorization no longer valid", data_source_id
        )
        raise
    except Exception as exc:
        reason = _readable_reason(exc)
        logger.exception("crawl failed for data source %s: %s", data_source_id, reason)
        # A separate transaction, after the failing one has unwound -- docs/18's
        # rollback pitfall. Writing this inside the block that raised would have
        # it rolled back by the very exception it records.
        await _mark_failed(
            session_factory,
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            data_source_id=data_source_id,
            reason=reason,
        )


def _readable_reason(exc: Exception) -> str:
    """Known failures keep their message; anything else is deliberately
    generic, because an unexpected traceback can carry internal paths and table
    names and the tenant is not the right audience for those."""
    if isinstance(exc, UnsafeCrawlTargetError | CrawlFailed):
        return str(exc)
    return "an unexpected error occurred while crawling this source"


async def _claim(
    session_factory: Callable[[], AsyncSession],
    *,
    tenant_id: UUID,
    actor_user_id: UUID,
    data_source_id: UUID,
) -> _DataSourceRow:
    """Marks the source `syncing` and returns what the crawl needs.

    Its own short transaction, so the console shows `syncing` immediately
    rather than after the whole crawl -- which for a 500-page site is the
    difference between visible progress and an apparently dead button.
    """
    async with session_factory() as session, session.begin():
        await establish_job_context(
            session, tenant_id=tenant_id, actor_user_id=actor_user_id
        )
        row = (
            await session.execute(
                text(
                    "SELECT ds.knowledge_base_id, ds.config, ds.created_by_membership_id, "
                    "       kb.vector_namespace "
                    "FROM data_sources ds "
                    "JOIN knowledge_bases kb ON kb.id = ds.knowledge_base_id "
                    "WHERE ds.id = :dsid AND ds.kind = 'url_crawl'"
                ),
                {"dsid": str(data_source_id)},
            )
        ).first()
        if row is None:
            # RLS-scoped, so this also covers "belongs to another tenant": the
            # job context was set from the claimed tenant, so a source outside
            # it is simply not visible.
            raise CrawlFailed(
                f"crawl source {data_source_id} not found or not in this tenant"
            )

        knowledge_base_id, config, membership_id, namespace = row
        urls = list(config.get("urls") or [])
        if not urls:
            raise CrawlFailed("this source has no URLs to crawl")

        await session.execute(
            text(
                "UPDATE data_sources SET sync_status = 'syncing', failure_reason = NULL, "
                "pages_discovered = 0, pages_indexed = 0 WHERE id = :dsid"
            ),
            {"dsid": str(data_source_id)},
        )

    return _DataSourceRow(
        knowledge_base_id=knowledge_base_id,
        vector_namespace=namespace,
        storage_prefix=f"{tenant_id}/{knowledge_base_id}",
        created_by_membership_id=membership_id,
        urls=urls,
        mode=CrawlMode(config.get("mode") or CrawlMode.URL_LIST.value),
    )


async def _crawl_and_index(
    session_factory: Callable[[], AsyncSession],
    dependencies: CrawlDependencies,
    *,
    tenant_id: UUID,
    actor_user_id: UUID,
    data_source_id: UUID,
    source: _DataSourceRow,
) -> tuple[int, int]:
    discovered = 0
    indexed = 0

    async for page in dependencies.crawler.crawl(
        urls=source.urls, mode=source.mode, limits=dependencies.limits
    ):
        discovered += 1
        try:
            await _index_one_page(
                session_factory,
                dependencies,
                tenant_id=tenant_id,
                actor_user_id=actor_user_id,
                data_source_id=data_source_id,
                source=source,
                page=page,
            )
            indexed += 1
        except JobAuthorizationError:
            # Authorization revoked mid-crawl -- stop, do not keep indexing.
            raise
        except Exception:
            # One unindexable page must not abandon the rest of the site. It is
            # counted as discovered but not indexed, which is what the console
            # shows the tenant.
            logger.exception("failed to index crawled page %s", page.url)

    return discovered, indexed


async def _index_one_page(
    session_factory: Callable[[], AsyncSession],
    dependencies: CrawlDependencies,
    *,
    tenant_id: UUID,
    actor_user_id: UUID,
    data_source_id: UUID,
    source: _DataSourceRow,
    page: CrawledPage,
) -> None:
    """One page, one transaction. See the module docstring for why."""
    body = page.markdown.encode("utf-8")
    checksum = hashlib.sha256(body).hexdigest()

    async with session_factory() as session, session.begin():
        context = await establish_job_context(
            session, tenant_id=tenant_id, actor_user_id=actor_user_id
        )
        document_id = await _upsert_document_row(
            session,
            context=context,
            data_source_id=data_source_id,
            source=source,
            page=page,
            size_bytes=len(body),
            checksum=checksum,
        )

        # Bytes before the row is usable, same ordering as upload: an orphaned
        # object is invisible dead weight, whereas a row pointing at bytes that
        # were never written is a document that fails for a reason nobody can
        # fix. Written inside the transaction because the row already exists by
        # now -- a failure here rolls the row back, leaving only the orphan.
        storage_path = f"{source.storage_prefix}/{document_id}"
        await dependencies.object_storage.put(
            path=storage_path, data=body, content_type=_CRAWLED_CONTENT_TYPE
        )

        await index_blocks(
            session,
            target=IndexingTarget(
                tenant_id=context.tenant_id,
                knowledge_base_id=source.knowledge_base_id,
                document_id=document_id,
                vector_namespace=source.vector_namespace,
            ),
            # A crawled page arrives as text and needs no parser. The URL is
            # carried as the source location so a citation can point at the
            # page a chunk came from, the same way a PDF chunk carries a page
            # number.
            blocks=[ParsedBlock(text=page.markdown, source_location=page.url)],
            chunker=dependencies.chunker,
            embedding_client=dependencies.embedding_client,
            vector_search=dependencies.vector_search,
        )

        await session.execute(
            text(
                "UPDATE documents SET status = 'ready', failure_reason = NULL "
                "WHERE id = :did"
            ),
            {"did": str(document_id)},
        )


async def _upsert_document_row(
    session: AsyncSession,
    *,
    context: VerifiedJobContext,
    data_source_id: UUID,
    source: _DataSourceRow,
    page: CrawledPage,
    size_bytes: int,
    checksum: str,
) -> UUID:
    """Re-crawling a page updates its document rather than adding a second one.

    Backed by `uq_documents_source_url_per_kb`, so this is enforced by the
    database and not merely by this query getting the lookup right.
    """
    existing = (
        await session.execute(
            text(
                "SELECT id FROM documents "
                "WHERE knowledge_base_id = :kbid AND source_url = :url "
                "  AND deleted_at IS NULL"
            ),
            {"kbid": str(source.knowledge_base_id), "url": page.url},
        )
    ).scalar_one_or_none()

    filename = _filename_for(page)

    if existing is not None:
        await session.execute(
            text(
                "UPDATE documents SET filename = :name, size_bytes = :size, "
                "  checksum = :sum, status = 'processing', failure_reason = NULL, "
                "  data_source_id = :dsid "
                "WHERE id = :did"
            ),
            {
                "name": filename,
                "size": size_bytes,
                "sum": checksum,
                "dsid": str(data_source_id),
                "did": str(existing),
            },
        )
        return UUID(str(existing))

    document_id = uuid4()
    await session.execute(
        text(
            "INSERT INTO documents "
            "(id, tenant_id, knowledge_base_id, uploaded_by_membership_id, filename, "
            " content_type, storage_path, size_bytes, status, checksum, "
            " data_source_id, source_url) "
            "VALUES (:id, :tid, :kbid, :mid, :name, :ctype, :path, :size, "
            "        'processing', :sum, :dsid, :url)"
        ),
        {
            "id": str(document_id),
            "tid": str(context.tenant_id),
            "kbid": str(source.knowledge_base_id),
            "mid": str(source.created_by_membership_id),
            "name": filename,
            "ctype": _CRAWLED_CONTENT_TYPE,
            "path": f"{source.storage_prefix}/{document_id}",
            "size": size_bytes,
            "sum": checksum,
            "dsid": str(data_source_id),
            "url": page.url,
        },
    )
    return document_id


def _filename_for(page: CrawledPage) -> str:
    """A display name for a document list. The page title if it has one, the
    URL otherwise -- never blank, because a row labelled nothing is a row
    nobody can identify."""
    if page.title:
        return page.title[:500]
    return page.url[:500]


async def _finish(
    session_factory: Callable[[], AsyncSession],
    *,
    tenant_id: UUID,
    actor_user_id: UUID,
    data_source_id: UUID,
    discovered: int,
    indexed: int,
) -> None:
    async with session_factory() as session, session.begin():
        await establish_job_context(
            session, tenant_id=tenant_id, actor_user_id=actor_user_id
        )
        await session.execute(
            text(
                "UPDATE data_sources SET sync_status = 'ready', last_synced_at = now(), "
                "  pages_discovered = :found, pages_indexed = :indexed, "
                "  failure_reason = NULL "
                "WHERE id = :dsid"
            ),
            {
                "found": discovered,
                "indexed": indexed,
                "dsid": str(data_source_id),
            },
        )


async def _mark_failed(
    session_factory: Callable[[], AsyncSession],
    *,
    tenant_id: UUID,
    actor_user_id: UUID,
    data_source_id: UUID,
    reason: str,
) -> None:
    """Best-effort, in its own transaction. If this fails there is nothing
    further to do but log -- raising would replace a specific crawl error with
    a generic database one."""
    try:
        async with session_factory() as session, session.begin():
            # A new transaction, so the RLS context must be set again --
            # `set_config(..., true)` did not survive the last one.
            await session.execute(
                text("SELECT set_config('app.user_id', :uid, true)"),
                {"uid": str(actor_user_id)},
            )
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tid, true)"),
                {"tid": str(tenant_id)},
            )
            await session.execute(
                text(
                    "UPDATE data_sources SET sync_status = 'error', "
                    "  failure_reason = :reason WHERE id = :dsid"
                ),
                {"dsid": str(data_source_id), "reason": reason},
            )
    except Exception:
        logger.exception(
            "could not record crawl failure for data source %s", data_source_id
        )
