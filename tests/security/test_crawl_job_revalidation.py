"""A crawl job re-validates its authorization, exactly as an upload job does.

Threat-model scenario 8 covers a background job whose authorization went stale
between enqueue and execution. Phase 11 closed it for document ingestion. This
phase adds a *second* job type, and a defence only one job honours is not a
defence: a tenant suspended mid-crawl would keep having pages fetched and
indexed by a worker running under an authorization nobody re-checked.

**The window is wider here, which is why it matters more.** A document parse is
seconds. A crawl runs for up to two hours, so a membership revoked five minutes
in leaves an hour and fifty-five minutes of indexing still to come.

These drive the real ``process_url_crawl`` against real Postgres with real RLS.
The ordering under test -- set the RLS context from the *claimed* tenant, then
validate -- is a property of the database session, and a fake session cannot
have it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from iam_platform.application.ai_resources.ports import CrawlLimits
from iam_platform.workers.job_context import JobAuthorizationError
from iam_platform.workers.jobs.process_url_crawl import process_url_crawl

pytestmark = pytest.mark.integration


class _NeverCalledCrawler:
    """Fails loudly if the crawl runs at all.

    Asserting only that an exception was raised would pass even if the crawler
    had already walked the site and indexed it before the refusal landed. The
    property under test is that *nothing was fetched*.
    """

    def __init__(self) -> None:
        self.called = False

    def crawl(self, **_: Any) -> Any:
        self.called = True
        raise AssertionError("the crawler must not run for an unauthorized job")


@dataclass
class _Deps:
    crawler: Any
    object_storage: Any = None
    chunker: Any = None
    embedding_client: Any = None
    vector_search: Any = None
    limits: CrawlLimits = CrawlLimits(
        max_depth=1,
        max_pages=1,
        page_timeout_seconds=5,
        job_timeout_seconds=30,
        respect_robots_txt=False,
        max_page_bytes=1024,
    )


@pytest_asyncio.fixture
async def seeded(migrator_engine: AsyncEngine) -> AsyncIterator[dict[str, UUID]]:
    """An active tenant/user/membership, a knowledge base, and a crawl source.

    Seeded through the migrator connection so the fixture is not itself subject
    to the RLS being tested -- the approach the RLS proof suite uses.
    """
    ids = {
        "tenant_id": uuid4(),
        "other_tenant_id": uuid4(),
        "user_id": uuid4(),
        "membership_id": uuid4(),
        "kb_id": uuid4(),
        "source_id": uuid4(),
    }
    async with migrator_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO users (id, email, status, security_stamp) "
                "VALUES (:id, :email, 'active', :stamp)"
            ),
            {
                "id": str(ids["user_id"]),
                "email": f"crawl-{ids['user_id']}@example.test",
                "stamp": str(uuid4()),
            },
        )
        for key in ("tenant_id", "other_tenant_id"):
            await conn.execute(
                text(
                    "INSERT INTO tenants (id, slug, display_name, status, owner_user_id) "
                    "VALUES (:id, :slug, 'Crawl Test', 'active', :owner)"
                ),
                {
                    "id": str(ids[key]),
                    "slug": f"crawl-{ids[key]}",
                    "owner": str(ids["user_id"]),
                },
            )
        await conn.execute(
            text(
                "INSERT INTO tenant_memberships "
                "(id, tenant_id, user_id, status, is_default, metadata, "
                " created_at, updated_at, joined_at) "
                "VALUES (:id, :tid, :uid, 'active', false, '{}'::jsonb, "
                "        now(), now(), now())"
            ),
            {
                "id": str(ids["membership_id"]),
                "tid": str(ids["tenant_id"]),
                "uid": str(ids["user_id"]),
            },
        )
        await conn.execute(
            text(
                "INSERT INTO knowledge_bases "
                "(id, tenant_id, name, owner_membership_id, visibility, vector_namespace) "
                "VALUES (:id, :tid, 'Crawl KB', :mid, 'tenant', :ns)"
            ),
            {
                "id": str(ids["kb_id"]),
                "tid": str(ids["tenant_id"]),
                "mid": str(ids["membership_id"]),
                "ns": f"{ids['tenant_id']}/{ids['kb_id']}",
            },
        )
        await conn.execute(
            text(
                "INSERT INTO data_sources "
                "(id, tenant_id, knowledge_base_id, kind, config, "
                " created_by_membership_id) "
                "VALUES (:id, :tid, :kbid, 'url_crawl', "
                "        '{\"urls\": [\"https://example.com/\"], \"mode\": \"url_list\"}'::jsonb, "
                "        :mid)"
            ),
            {
                "id": str(ids["source_id"]),
                "tid": str(ids["tenant_id"]),
                "kbid": str(ids["kb_id"]),
                "mid": str(ids["membership_id"]),
            },
        )

    yield ids

    async with migrator_engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM data_sources WHERE tenant_id = :tid"),
            {"tid": str(ids["tenant_id"])},
        )
        await conn.execute(
            text("DELETE FROM knowledge_bases WHERE tenant_id = :tid"),
            {"tid": str(ids["tenant_id"])},
        )
        await conn.execute(
            text("DELETE FROM tenant_memberships WHERE user_id = :uid"),
            {"uid": str(ids["user_id"])},
        )
        await conn.execute(
            text("DELETE FROM tenants WHERE id = ANY(:ids)"),
            {"ids": [str(ids["tenant_id"]), str(ids["other_tenant_id"])]},
        )
        await conn.execute(
            text("DELETE FROM users WHERE id = :uid"), {"uid": str(ids["user_id"])}
        )


def _session_factory(engine: AsyncEngine) -> Any:
    def factory() -> AsyncSession:
        return AsyncSession(engine)

    return factory


async def _expect_refusal(
    engine: AsyncEngine, *, tenant_id: UUID, user_id: UUID, source_id: UUID
) -> None:
    crawler = _NeverCalledCrawler()
    with pytest.raises(JobAuthorizationError):
        await process_url_crawl(
            _session_factory(engine),
            _Deps(crawler=crawler),  # type: ignore[arg-type]
            tenant_id=tenant_id,
            actor_user_id=user_id,
            data_source_id=source_id,
        )
    assert not crawler.called, "authorization was refused but the crawl ran anyway"


class TestCrawlJobAuthorizationRevalidation:
    async def test_a_suspended_tenant_stops_the_crawl(
        self, engine: AsyncEngine, migrator_engine: AsyncEngine, seeded: dict[str, UUID]
    ) -> None:
        async with migrator_engine.begin() as conn:
            await conn.execute(
                text("UPDATE tenants SET status = 'suspended' WHERE id = :tid"),
                {"tid": str(seeded["tenant_id"])},
            )

        await _expect_refusal(
            engine,
            tenant_id=seeded["tenant_id"],
            user_id=seeded["user_id"],
            source_id=seeded["source_id"],
        )

    async def test_a_revoked_membership_stops_the_crawl(
        self, engine: AsyncEngine, migrator_engine: AsyncEngine, seeded: dict[str, UUID]
    ) -> None:
        """The mid-crawl revocation case: this is what a two-hour job makes
        expensive to get wrong."""
        async with migrator_engine.begin() as conn:
            await conn.execute(
                text("UPDATE tenant_memberships SET status = 'revoked' WHERE id = :mid"),
                {"mid": str(seeded["membership_id"])},
            )

        await _expect_refusal(
            engine,
            tenant_id=seeded["tenant_id"],
            user_id=seeded["user_id"],
            source_id=seeded["source_id"],
        )

    async def test_a_suspended_user_stops_the_crawl(
        self, engine: AsyncEngine, migrator_engine: AsyncEngine, seeded: dict[str, UUID]
    ) -> None:
        async with migrator_engine.begin() as conn:
            await conn.execute(
                text("UPDATE users SET status = 'suspended' WHERE id = :uid"),
                {"uid": str(seeded["user_id"])},
            )

        await _expect_refusal(
            engine,
            tenant_id=seeded["tenant_id"],
            user_id=seeded["user_id"],
            source_id=seeded["source_id"],
        )

    async def test_a_deleted_user_stops_the_crawl(
        self, engine: AsyncEngine, migrator_engine: AsyncEngine, seeded: dict[str, UUID]
    ) -> None:
        async with migrator_engine.begin() as conn:
            await conn.execute(
                text("UPDATE users SET deleted_at = now() WHERE id = :uid"),
                {"uid": str(seeded["user_id"])},
            )

        await _expect_refusal(
            engine,
            tenant_id=seeded["tenant_id"],
            user_id=seeded["user_id"],
            source_id=seeded["source_id"],
        )

    async def test_a_payload_claiming_another_tenant_is_refused(
        self, engine: AsyncEngine, seeded: dict[str, UUID]
    ) -> None:
        """A tampered payload naming a tenant the actor has no membership in.

        The RLS context is set from the *claimed* tenant before validation, so
        even the query that rejects this runs with no more reach than the
        identity being claimed.
        """
        await _expect_refusal(
            engine,
            tenant_id=seeded["other_tenant_id"],
            user_id=seeded["user_id"],
            source_id=seeded["source_id"],
        )

    async def test_an_authorized_job_reaches_the_crawler(
        self, engine: AsyncEngine, seeded: dict[str, UUID]
    ) -> None:
        """The positive control.

        Without it, every refusal above could be passing because the job always
        raises -- and a crawler that never runs is not a security property, it
        is a broken feature. Here the crawl *is* reached: `_NeverCalledCrawler`
        records the call and raises, which `process_url_crawl` records as a
        source failure rather than re-raising.
        """
        crawler = _NeverCalledCrawler()

        await process_url_crawl(
            _session_factory(engine),
            _Deps(crawler=crawler),  # type: ignore[arg-type]
            tenant_id=seeded["tenant_id"],
            actor_user_id=seeded["user_id"],
            data_source_id=seeded["source_id"],
        )

        assert crawler.called, "an authorized job must actually reach the crawler"
