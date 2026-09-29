"""The retrieval tester's query embedding is metered.

It was the last provider spend recorded nowhere: every test in the console's
search box embeds the typed query. One `search` row per test, embedding only,
written after the search whatever happened to it -- and nothing at all when
the search was refused before reaching the provider.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from iam_platform.application.ai_resources.exceptions import KnowledgeBaseNotFoundError
from iam_platform.application.ai_resources.manage_knowledge_base import (
    QueryKnowledgeBase,
    QueryKnowledgeBaseQuery,
)
from iam_platform.application.ai_resources.ports import (
    ANSWER_CHANNELS,
    SEARCH_CHANNEL,
    TokenUsage,
    UsageEvent,
)
from iam_platform.core.clock import FixedClock
from iam_platform.domain.ai_resources.entities import ResourceVisibility
from tests.unit.ai_resources.fakes import FakeAiResourceUnitOfWork
from tests.unit.ai_resources.test_knowledge_base_and_secrets import (
    QUERY,
    _seed_knowledge_base,
    _seed_member,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 28, 12, tzinfo=UTC)


class _BilledSearch:
    """Charges for the query embedding as the real adapter does."""

    def __init__(self, *, fail_after_embedding: bool = False) -> None:
        self.queried: list[str] = []
        self._fail = fail_after_embedding

    async def query(
        self,
        *,
        namespace: str,
        query_text: str,
        top_k: int,
        usage: TokenUsage | None = None,
    ) -> list[tuple[UUID, float]]:
        self.queried.append(namespace)
        if usage is not None:
            usage.input_tokens += 8
            usage.total += 8
            usage.embedding_tokens += 8
            usage.embedding_model = "text-embedding-3-large"
        if self._fail:
            raise RuntimeError("vector store unavailable")
        return []


class _UnmeteredSearch:
    """The port as it was: no `usage` keyword at all."""

    async def query(
        self, *, namespace: str, query_text: str, top_k: int
    ) -> list[tuple[UUID, float]]:
        return []


class _Ledger:
    def __init__(self) -> None:
        self.rows: list[UsageEvent] = []

    async def record(self, event: UsageEvent) -> None:
        self.rows.append(event)


def _setup(visible: bool = True):  # type: ignore[no-untyped-def]
    uow = FakeAiResourceUnitOfWork()
    tenant_id = uuid4()
    user_id, membership = _seed_member(uow, tenant_id)
    if visible:
        kb = _seed_knowledge_base(uow, tenant_id, membership.id)
    else:
        _, other = _seed_member(uow, tenant_id)
        kb = _seed_knowledge_base(uow, tenant_id, other.id, visibility=ResourceVisibility.RESTRICTED)
    query = QueryKnowledgeBaseQuery(
        actor_user_id=str(user_id),
        tenant_id=str(tenant_id),
        knowledge_base_id=str(kb.id),
        permissions=frozenset({QUERY}),
        query_text="What discounts do you offer?",
    )
    return uow, tenant_id, query


class TestASearchTestIsMetered:
    async def test_one_search_row_with_the_embedding_it_cost(self) -> None:
        uow, tenant_id, query = _setup()
        ledger = _Ledger()
        use_case = QueryKnowledgeBase(
            uow, _BilledSearch(), usage_ledger=ledger, clock=FixedClock(NOW)  # type: ignore[arg-type]
        )
        await use_case.execute(query)

        (row,) = ledger.rows
        assert row.channel == SEARCH_CHANNEL
        assert row.tenant_id == tenant_id
        assert (row.input_tokens, row.embedding_tokens, row.total_tokens) == (8, 8, 8)
        assert row.embedding_model == "text-embedding-3-large"
        assert row.occurred_at == NOW

    async def test_it_is_not_part_of_the_chat_allowance(self) -> None:
        # The Redis seeds read these channels only; a search test is not one.
        assert SEARCH_CHANNEL not in ANSWER_CHANNELS

    async def test_a_search_that_fails_after_embedding_is_still_billed(self) -> None:
        uow, _, query = _setup()
        ledger = _Ledger()
        with pytest.raises(RuntimeError):
            await QueryKnowledgeBase(
                uow, _BilledSearch(fail_after_embedding=True), usage_ledger=ledger  # type: ignore[arg-type]
            ).execute(query)
        assert [r.total_tokens for r in ledger.rows] == [8]

    async def test_a_refused_search_records_nothing(self) -> None:
        uow, _, query = _setup(visible=False)
        ledger = _Ledger()
        search = _BilledSearch()
        with pytest.raises(KnowledgeBaseNotFoundError):
            await QueryKnowledgeBase(uow, search, usage_ledger=ledger).execute(query)  # type: ignore[arg-type]
        assert search.queried == [] and ledger.rows == []

    async def test_without_a_ledger_the_call_is_exactly_what_it_was(self) -> None:
        uow, _, query = _setup()
        # Would raise TypeError if a `usage` keyword were sent.
        assert await QueryKnowledgeBase(uow, _UnmeteredSearch()).execute(query) == []  # type: ignore[arg-type]


class _Embedder:
    dimensions = 4

    def __init__(self) -> None:
        self.meters: list[TokenUsage | None] = []

    async def embed(self, text: str, *, usage: TokenUsage | None = None) -> list[float]:
        self.meters.append(usage)
        if usage is not None:
            usage.input_tokens += 8
        return [0.0, 0.0, 0.0, 1.0]


class _QdrantStub:
    async def collection_exists(self, name: str) -> bool:
        return True

    async def query_points(self, **kwargs: object) -> object:
        from types import SimpleNamespace

        return SimpleNamespace(points=[])


class TestTheQdrantAdapterPassesTheMeter:
    async def test_the_query_embedding_is_charged_to_the_callers_meter(self) -> None:
        from iam_platform.core.config import QdrantSettings
        from iam_platform.infrastructure.vector.qdrant_search import QdrantVectorSearchClient

        embedder = _Embedder()
        client = QdrantVectorSearchClient(QdrantSettings(), embedding_client=embedder, client=_QdrantStub())  # type: ignore[arg-type]
        meter = TokenUsage()
        await client.query(namespace=f"{uuid4()}/{uuid4()}", query_text="hi", top_k=5, usage=meter)
        assert embedder.meters == [meter] and meter.input_tokens == 8
