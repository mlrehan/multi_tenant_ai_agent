"""Embedding spend on ingestion is metered, and kept out of the chat allowance.

Indexing a knowledge base embeds every chunk through the provider. Until this
was metered, the dashboards showed about an eighth of the tokens actually sent
(319,895 indexed against 39,445 answered, on the dev tenant).

Two properties matter, and each has a test that fails without it:

* **Spend is recorded even when the document then fails.** The provider bills
  the embeddings when it returns them; a job whose transaction later rolls
  back still owes them.
* **Ingestion rows never reach the Redis seed.** The chat allowance and the
  daily message count are rebuilt from the ledger. Counting ingestion there
  would charge a crawl against the chatbot and count each page as a message.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from iam_platform.application.ai_resources.ports import (
    INGESTION_CHANNEL,
    CrawledPage,
    CrawlMode,
    TokenUsage,
    UsageEvent,
)
from iam_platform.core.config import IngestionSettings
from iam_platform.infrastructure.db.repositories.platform_activity import (
    SqlPlatformActivityReader,
)
from iam_platform.infrastructure.db.usage_ledger import SqlUsageLedger
from iam_platform.infrastructure.parsing.chunking import TokenAwareChunker
from iam_platform.workers.jobs.process_document_upload import process_document_upload
from iam_platform.workers.jobs.process_url_crawl import (
    CrawlDependencies,
    _DataSourceRow,
    _index_one_page,
)
from tests.unit.ai_resources.test_ingestion_job import (
    ACTOR_ID,
    DOCUMENT_ID,
    KB_ID,
    NAMESPACE,
    TENANT_ID,
    _dependencies,
    _factory,
    _FakeResult,
    _FakeSession,
    _FakeStorage,
    _FakeVectorSearch,
    _RaisingSession,
)

pytestmark = pytest.mark.unit

TOKENS_PER_TEXT = 11


class _BilledEmbeddings:
    """Charges a fixed amount per embedded text, as the real adapter reports."""

    dimensions = 4

    def __init__(self) -> None:
        self.metered_calls = 0

    async def embed(self, text: str, **kwargs: object) -> list[float]:
        return [1.0, 0.0, 0.0, 0.0]

    async def embed_batch(
        self, texts: list[str], *, usage: TokenUsage | None = None, **kwargs: object
    ) -> list[list[float]]:
        if usage is not None:
            self.metered_calls += 1
            usage.input_tokens += TOKENS_PER_TEXT * len(texts)
            usage.total += TOKENS_PER_TEXT * len(texts)
        return [[1.0, 0.0, 0.0, 0.0] for _ in texts]


class _Ledger:
    def __init__(self, *, broken: bool = False) -> None:
        self.events: list[UsageEvent] = []
        self._broken = broken

    async def record(self, event: UsageEvent) -> None:
        if self._broken:
            raise RuntimeError("ledger unavailable")
        self.events.append(event)


async def _upload(session: _FakeSession, ledger: _Ledger | None) -> _BilledEmbeddings:
    embeddings = _BilledEmbeddings()
    await process_document_upload(
        _factory(session),
        _dependencies(embedding_client=embeddings, usage_ledger=ledger),
        tenant_id=TENANT_ID,
        actor_user_id=ACTOR_ID,
        document_id=DOCUMENT_ID,
    )
    return embeddings


class TestAnUploadIsMetered:
    async def test_one_ingestion_row_with_what_the_embeddings_cost(self) -> None:
        ledger = _Ledger()
        await _upload(_FakeSession(), ledger)

        (event,) = ledger.events
        assert event.channel == INGESTION_CHANNEL
        assert event.tenant_id == TENANT_ID
        assert event.total_tokens == TOKENS_PER_TEXT  # one chunk
        assert event.input_tokens == TOKENS_PER_TEXT
        assert event.output_tokens == 0
        assert event.model_configuration_id is None

    async def test_a_document_that_fails_after_embedding_is_still_billed(self) -> None:
        """The chunk INSERT fails *after* the provider was paid; the job's
        transaction rolls back, and the spend must survive that."""
        ledger = _Ledger()
        session = _RaisingSession()
        await _upload(session, ledger)

        assert any("status = 'failed'" in s for s in session.statements)
        assert [e.total_tokens for e in ledger.events] == [TOKENS_PER_TEXT]

    async def test_a_broken_ledger_does_not_fail_the_document(self) -> None:
        session = _FakeSession()
        await _upload(session, _Ledger(broken=True))
        assert any("status = 'ready'" in s for s in session.statements)

    async def test_nothing_embedded_means_nothing_recorded(self) -> None:
        from tests.unit.ai_resources.test_ingestion_job import _FakeParser

        ledger = _Ledger()
        embeddings = _BilledEmbeddings()
        await process_document_upload(
            _factory(_FakeSession()),
            _dependencies(
                embedding_client=embeddings,
                usage_ledger=ledger,
                parser=_FakeParser(blocks=[]),
            ),
            tenant_id=TENANT_ID,
            actor_user_id=ACTOR_ID,
            document_id=DOCUMENT_ID,
        )
        assert ledger.events == []


class _CrawlSession(_FakeSession):
    """Answers the crawl's "is this page already a document?" lookup."""

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> Any:
        result = await super().execute(statement, params)
        return _ScalarResult(result)


class _ScalarResult:
    def __init__(self, inner: _FakeResult) -> None:
        self._inner = inner

    def first(self) -> Any:
        return self._inner.first()

    def scalar(self) -> Any:
        return self._inner.scalar()

    def scalar_one_or_none(self) -> Any:
        return None


class TestACrawledPageIsMetered:
    async def test_each_page_records_its_own_row(self) -> None:
        ledger = _Ledger()
        dependencies = CrawlDependencies(
            crawler=None,  # type: ignore[arg-type]  -- not used for one page
            object_storage=_FakeStorage(),
            chunker=TokenAwareChunker(IngestionSettings()),
            embedding_client=_BilledEmbeddings(),
            vector_search=_FakeVectorSearch(),
            limits=None,  # type: ignore[arg-type]
            usage_ledger=ledger,
        )
        source = _DataSourceRow(
            knowledge_base_id=KB_ID,
            vector_namespace=NAMESPACE,
            storage_prefix="t/kb",
            created_by_membership_id=uuid4(),
            urls=["https://site.example/"],
            mode=CrawlMode.URL_LIST,
        )
        for url in ("https://site.example/a", "https://site.example/b"):
            await _index_one_page(
                _factory(_CrawlSession()),
                dependencies,
                tenant_id=TENANT_ID,
                actor_user_id=ACTOR_ID,
                data_source_id=uuid4(),
                source=source,
                page=CrawledPage(url=url, title="Page", markdown="Opening hours are 8 to 6."),
            )

        assert [e.channel for e in ledger.events] == [INGESTION_CHANNEL, INGESTION_CHANNEL]
        assert all(e.total_tokens == TOKENS_PER_TEXT for e in ledger.events)


# -- the seed never sees ingestion -------------------------------------------


class _CapturingSession:
    """Records the statement a ledger query sends, and answers it with zeros."""

    def __init__(self, sink: list[str]) -> None:
        self._sink = sink

    async def __aenter__(self) -> _CapturingSession:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def begin(self) -> _CapturingSession:
        return self

    async def execute(self, statement: Any, params: Any = None) -> Any:
        self._sink.append(_sql(statement))
        return _Zeros()

    async def scalar(self, statement: Any, params: Any = None) -> int:
        self._sink.append(_sql(statement))
        return 0


class _Zeros:
    def one(self) -> tuple[int, int, int]:
        return (0, 0, 0)

    def all(self) -> list[Any]:
        return []


def _sql(statement: Any) -> str:
    try:
        return str(
            statement.compile(
                dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
            )
        )
    except Exception:
        return str(statement)


SINCE = datetime(2026, 9, 1, tzinfo=UTC)


class TestTheSeedCountsAnswersOnly:
    async def test_the_monthly_token_seed_excludes_ingestion(self) -> None:
        sent: list[str] = []
        await SqlUsageLedger(lambda: _CapturingSession(sent)).tokens_since(  # type: ignore[arg-type,return-value]
            tenant_id=TENANT_ID, since=SINCE
        )
        (query,) = [s for s in sent if "ai_usage_events" in s]
        assert "channel IN ('console', 'widget')" in query

    async def test_the_daily_message_seed_excludes_ingestion(self) -> None:
        sent: list[str] = []
        await SqlUsageLedger(lambda: _CapturingSession(sent)).answers_since(  # type: ignore[arg-type,return-value]
            tenant_id=TENANT_ID, since=SINCE
        )
        (query,) = [s for s in sent if "ai_usage_events" in s]
        assert "channel IN ('console', 'widget')" in query

    async def test_the_activity_token_series_excludes_ingestion(self) -> None:
        sent: list[str] = []
        reader = SqlPlatformActivityReader(_CapturingSession(sent))  # type: ignore[arg-type]
        await reader.daily_counts(since=SINCE)
        (query,) = sent
        usage_part = query[query.index("FROM ai_usage_events") :][:200]
        assert "channel IN ('console','widget')" in usage_part

    async def test_the_ingestion_total_reads_only_ingestion(self) -> None:
        captured: list[tuple[str, dict[str, Any]]] = []

        class _Session(_CapturingSession):
            async def execute(self, statement: Any, params: Any = None) -> Any:
                captured.append((str(statement), dict(params or {})))
                return _Zeros()

        reader = SqlPlatformActivityReader(_Session([]), tenant_id=TENANT_ID)  # type: ignore[arg-type]
        assert await reader.ingestion_tokens_since(since=SINCE) == {}
        (query, params) = captured[0]
        assert "channel = :channel" in query and "tenant_id = :tenant_id" in query
        assert params["channel"] == INGESTION_CHANNEL
        assert params["tenant_id"] == TENANT_ID


class TestTheLedgerWritesWithoutTheOrmFlush:
    async def test_record_is_a_plain_insert(self) -> None:
        """An ORM flush needs every foreign-keyed table registered, and the
        worker never imports `tenants` -- every ingestion row failed there,
        silently, until this became a Core INSERT."""
        sent: list[str] = []
        await SqlUsageLedger(lambda: _CapturingSession(sent)).record(  # type: ignore[arg-type,return-value]
            UsageEvent(
                tenant_id=TENANT_ID,
                channel=INGESTION_CHANNEL,
                model_configuration_id=None,
                input_tokens=10,
                output_tokens=0,
                total_tokens=10,
                occurred_at=SINCE,
            )
        )
        (insert,) = [s for s in sent if s.startswith("INSERT INTO ai_usage_events")]
        assert "'ingestion'" in insert
