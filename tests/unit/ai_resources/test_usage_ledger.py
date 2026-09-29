"""The usage counters survive losing Redis, and every answer leaves a ledger row.

The incident behind these: Redis ran with persistence off, a restart wiped it,
and the platform dashboard read "0 / 1,000,000 tokens" for a tenant that had
spent tens of thousands -- while the same wipe silently handed every tenant a
fresh monthly allowance and daily message cap. Redis is still the counter the
answer path reads; these pin that a *missing* key is rebuilt from the Postgres
ledger before it is read or incremented, and that the answer path writes the
ledger rows it is rebuilt from.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest

from iam_platform.application.ai_resources.answer_question import (
    AnswerQuestion,
    AnswerStream,
)
from iam_platform.application.ai_resources.ports import TokenUsage, UsageEvent
from iam_platform.application.ai_resources.public_chat import AskWidget, AskWidgetCommand
from iam_platform.domain.ai_resources.entities import ChatWidget, MessageRole
from iam_platform.domain.tenancy.entitlements import TenantEntitlements
from iam_platform.infrastructure.cache.tenant_quota import (
    QuotaUnavailableError,
    RedisTenantQuotaStore,
    _day_key,
    _month_key,
)
from iam_platform.infrastructure.cache.token_usage import RedisTokenUsageStore, _key
from tests.unit.ai_resources.test_answer_question import _chunk, _FakeVectorSearch
from tests.unit.ai_resources.test_widget_conversation_persistence import (
    ORIGIN,
    _FakeConversations,
    _FakeLookup,
    _FakeMessages,
    _FakeQuota,
    _FixedClock,
    _uow_factory,
    _widget,
)

pytestmark = pytest.mark.asyncio

TENANT = uuid4()


# -- a small in-memory Redis ----------------------------------------------------


class _Pipeline:
    def __init__(self, redis: _FakeRedis) -> None:
        self._redis = redis
        self._ops: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        def queue(*args: object, **kwargs: object) -> _Pipeline:
            self._ops.append((name, args, kwargs))
            return self

        return queue

    async def execute(self) -> list[object]:
        return [await getattr(self._redis, n)(*a, **k) for n, a, k in self._ops]


class _FakeRedis:
    """Enough of redis.asyncio for the two stores. Values are kept as strings,
    as Redis returns them, so the stores' `int(raw)` parsing is exercised."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def pipeline(self) -> _Pipeline:
        return _Pipeline(self)

    async def exists(self, key: str) -> int:
        return int(key in self.data)

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def mget(self, *keys: str) -> list[str | None]:
        return [self.data.get(k) for k in keys]

    async def set(self, key: str, value: object, nx: bool = False, ex: int | None = None) -> bool:
        del ex
        if nx and key in self.data:
            return False
        self.data[key] = str(value)
        return True

    async def incrby(self, key: str, amount: int) -> int:
        self.data[key] = str(int(self.data.get(key, "0")) + amount)
        return int(self.data[key])

    async def incr(self, key: str) -> int:
        return await self.incrby(key, 1)

    async def decr(self, key: str) -> int:
        return await self.incrby(key, -1)

    async def expire(self, key: str, seconds: int, nx: bool = False) -> bool:
        del key, seconds, nx
        return True


@dataclass
class _FakeLedger:
    """A ledger holding `answers` rows today and `tokens` this month."""

    answers: int = 0
    tokens: TokenUsage = field(default_factory=TokenUsage)
    per_configuration: int = 0
    broken: bool = False
    since_seen: list[datetime] = field(default_factory=list)

    async def answers_since(self, *, tenant_id: UUID, since: datetime) -> int:
        del tenant_id
        self._check()
        self.since_seen.append(since)
        return self.answers

    async def tokens_since(self, *, tenant_id: UUID, since: datetime) -> TokenUsage:
        del tenant_id, since
        self._check()
        return self.tokens

    async def configuration_tokens_since(
        self, *, tenant_id: UUID, model_configuration_id: UUID, since: datetime
    ) -> int:
        del tenant_id, model_configuration_id, since
        self._check()
        return self.per_configuration

    async def record(self, event: UsageEvent) -> None:
        del event

    def _check(self) -> None:
        if self.broken:
            raise RuntimeError("postgres is down")


# -- the counters are rebuilt, not restarted -----------------------------------


class TestAWipedCounterIsRebuiltFromTheLedger:
    async def test_the_months_tokens_read_back_after_redis_lost_them(self) -> None:
        redis = _FakeRedis()
        ledger = _FakeLedger(tokens=TokenUsage(total=59_623, input_tokens=50_000, output_tokens=9_000))
        store = RedisTenantQuotaStore(redis, ledger=ledger)  # type: ignore[arg-type]

        assert await store.tokens_used_this_month(tenant_id=TENANT) == 59_623
        breakdown = await store.token_breakdown(tenant_id=TENANT)
        assert (breakdown.input_tokens, breakdown.output_tokens) == (50_000, 9_000)

    async def test_a_recording_lands_on_top_of_the_history_not_from_zero(self) -> None:
        """An `INCRBY` on a missing key would create it holding only this
        answer -- and every later read would trust that number."""
        redis = _FakeRedis()
        store = RedisTenantQuotaStore(redis, ledger=_FakeLedger(tokens=TokenUsage(total=1_000)))  # type: ignore[arg-type]

        await store.record_tokens(tenant_id=TENANT, usage=TokenUsage(total=50))

        assert redis.data[_month_key(TENANT)] == "1050"

    async def test_a_reservation_counts_the_days_answers_before_it(self) -> None:
        """49 answered today, limit 50: the next is allowed, the one after is
        not -- rather than the wipe granting a fresh 50."""
        redis = _FakeRedis()
        store = RedisTenantQuotaStore(redis, ledger=_FakeLedger(answers=49))  # type: ignore[arg-type]

        assert await store.consume_message(tenant_id=TENANT, limit=50) is True
        assert await store.consume_message(tenant_id=TENANT, limit=50) is False

    async def test_a_present_key_is_not_overwritten_by_the_ledger(self) -> None:
        """While Redis holds the window it is the authority -- it includes
        reservations for answers still in flight, which the ledger cannot."""
        redis = _FakeRedis()
        redis.data[_day_key(TENANT)] = "7"
        store = RedisTenantQuotaStore(redis, ledger=_FakeLedger(answers=3))  # type: ignore[arg-type]

        assert await store.messages_used_today(tenant_id=TENANT) == 7

    async def test_the_day_window_starts_at_the_tenants_own_midnight(self) -> None:
        zone = ZoneInfo("Asia/Dhaka")
        ledger = _FakeLedger()
        store = RedisTenantQuotaStore(_FakeRedis(), ledger=ledger)  # type: ignore[arg-type]

        await store.messages_used_today(tenant_id=TENANT, zone=zone)

        (since,) = ledger.since_seen
        local = since.astimezone(zone)
        assert (local.hour, local.minute, local.second) == (0, 0, 0)
        assert local.date() == datetime.now(zone).date()

    async def test_the_per_model_budget_is_rebuilt_too(self) -> None:
        redis = _FakeRedis()
        config = uuid4()
        store = RedisTokenUsageStore(redis, ledger=_FakeLedger(per_configuration=4_200))  # type: ignore[arg-type]

        assert await store.read(tenant_id=TENANT, model_configuration_id=config) == 4_200
        await store.record(tenant_id=TENANT, model_configuration_id=config, tokens=100)
        assert redis.data[_key(TENANT, config)] == "4300"


class TestALedgerOutageFailsTheRightWay:
    async def test_a_reservation_that_cannot_be_confirmed_is_refused(self) -> None:
        store = RedisTenantQuotaStore(_FakeRedis(), ledger=_FakeLedger(broken=True))  # type: ignore[arg-type]
        assert await store.consume_message(tenant_id=TENANT, limit=50) is False

    async def test_a_read_that_cannot_be_confirmed_raises_rather_than_reading_zero(self) -> None:
        store = RedisTenantQuotaStore(_FakeRedis(), ledger=_FakeLedger(broken=True))  # type: ignore[arg-type]
        with pytest.raises(QuotaUnavailableError):
            await store.tokens_used_this_month(tenant_id=TENANT)

    async def test_a_recording_that_cannot_seed_leaves_the_key_absent(self) -> None:
        """Creating the key from this answer alone would hide the month's
        history from every later read; leaving it absent means the next read
        seeds from the ledger, which by then holds this answer's row."""
        redis = _FakeRedis()
        store = RedisTenantQuotaStore(redis, ledger=_FakeLedger(broken=True))  # type: ignore[arg-type]

        await store.record_tokens(tenant_id=TENANT, usage=TokenUsage(total=50))

        assert _month_key(TENANT) not in redis.data


class TestReleasingALostReservation:
    async def test_releasing_after_a_wipe_does_not_create_a_negative_count(self) -> None:
        redis = _FakeRedis()
        store = RedisTenantQuotaStore(redis, ledger=_FakeLedger(answers=5))  # type: ignore[arg-type]

        await store.release_message(tenant_id=TENANT)

        assert _day_key(TENANT) not in redis.data
        assert await store.messages_used_today(tenant_id=TENANT) == 5


class TestWithoutALedgerNothingChanges:
    async def test_a_missing_key_still_reads_zero(self) -> None:
        store = RedisTenantQuotaStore(_FakeRedis())  # type: ignore[arg-type]
        assert await store.tokens_used_this_month(tenant_id=TENANT) == 0


# -- the answer path writes the rows -------------------------------------------


class _Entitlements:
    async def get_for_tenant(self, tenant_id: UUID) -> TenantEntitlements:
        now = datetime.now(UTC)
        return TenantEntitlements(
            id=uuid4(), tenant_id=tenant_id, max_tokens_per_month=None,
            created_at=now, updated_at=now,
        )


class _Uow:
    entitlements = _Entitlements()

    async def __aenter__(self) -> _Uow:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class _Reranker:
    async def rerank(self, *, query: str, chunks: list, top_n: int) -> list:  # type: ignore[type-arg]
        from iam_platform.application.ai_resources.ports import RerankedChunk

        del query
        return [RerankedChunk(chunk=c, relevance=c.score) for c in chunks[:top_n]]


class _BilledChatModel:
    """Fills the meter in the way the OpenAI adapter does."""

    async def _stream(self, usage: TokenUsage | None) -> AsyncIterator[str]:
        yield "Open 9 to 5 [1]."
        if usage is not None:
            usage.input_tokens += 900
            usage.output_tokens += 100
            usage.total += 1_000

    def stream_answer(self, *, usage: TokenUsage | None = None, **kwargs: object) -> AsyncIterator[str]:
        del kwargs
        return self._stream(usage)


class _Events:
    """One shared, ordered log across the Redis counter and the ledger."""

    def __init__(self, ledger_broken: bool = False) -> None:
        self.log: list[str] = []
        self.rows: list[UsageEvent] = []
        self._ledger_broken = ledger_broken

    # tenant quota
    async def tokens_used_this_month(self, *, tenant_id: UUID) -> int:
        del tenant_id
        return 0

    async def record_tokens(self, *, tenant_id: UUID, usage: TokenUsage) -> None:
        del tenant_id, usage
        self.log.append("redis")

    # ledger
    async def record(self, event: UsageEvent) -> None:
        if self._ledger_broken:
            raise RuntimeError("postgres is down")
        self.log.append("ledger")
        self.rows.append(event)


def _pipeline(events: _Events, chunks: list) -> AnswerQuestion:  # type: ignore[type-arg]
    return AnswerQuestion(
        lambda _a, _t: _Uow(),  # type: ignore[arg-type,return-value]
        _FakeVectorSearch(chunks=chunks),  # type: ignore[arg-type]
        _Reranker(),  # type: ignore[arg-type]
        _BilledChatModel(),  # type: ignore[arg-type]
        tenant_quota=events,
        usage_ledger=events,  # type: ignore[arg-type]
    )


async def _drain(stream: AnswerStream) -> str:
    return "".join([p async for p in stream.tokens])


class TestEveryAnswerLeavesALedgerRow:
    async def test_a_generated_answer_records_its_full_cost(self) -> None:
        events = _Events()
        stream = await _pipeline(events, [_chunk("We open at 9.")]).answer_from_namespace(
            "When do you open?", namespace=f"{TENANT}/{uuid4()}", tenant_id=TENANT
        )
        await _drain(stream)

        (row,) = events.rows
        assert row.tenant_id == TENANT
        assert (row.input_tokens, row.output_tokens, row.total_tokens) == (900, 100, 1_000)
        assert row.channel == "console"

    async def test_redis_is_recorded_before_the_ledger(self) -> None:
        """The other order double-counts: a counter seeding itself from the
        ledger would already see this answer's row, then add it again."""
        events = _Events()
        stream = await _pipeline(events, [_chunk("We open at 9.")]).answer_from_namespace(
            "When do you open?", namespace=f"{TENANT}/{uuid4()}", tenant_id=TENANT
        )
        await _drain(stream)

        assert events.log == ["redis", "ledger"]

    async def test_a_question_refused_for_lack_of_passages_is_still_recorded(self) -> None:
        """It consumed a daily message; without a row the day's count could
        not be rebuilt after a wipe."""
        events = _Events()
        stream = await _pipeline(events, []).answer_from_namespace(
            "Anything?", namespace=f"{TENANT}/{uuid4()}", tenant_id=TENANT
        )
        await _drain(stream)

        assert len(events.rows) == 1

    async def test_a_ledger_outage_does_not_fail_the_answer(self) -> None:
        events = _Events(ledger_broken=True)
        stream = await _pipeline(events, [_chunk("We open at 9.")]).answer_from_namespace(
            "When do you open?", namespace=f"{TENANT}/{uuid4()}", tenant_id=TENANT
        )

        assert await _drain(stream) == "Open 9 to 5 [1]."


# -- the widget labels its rows and stores its cost -----------------------------


@dataclass
class _CapturingPipeline:
    kwargs: dict[str, object] = field(default_factory=dict)

    async def answer_from_namespace(self, question: str, *, namespace: str, **kwargs: object) -> AnswerStream:
        del question, namespace
        self.kwargs = kwargs

        async def _tokens() -> AsyncIterator[str]:
            yield "Open 9 to 5."

        return AnswerStream(
            citations=[], tokens=_tokens(), usage=TokenUsage(total=1_234)
        )


class _TestWidgetPath:
    """Shared widget harness; subclassed by other test modules."""

    async def _ask(self, pipeline: _CapturingPipeline, widget: ChatWidget):  # type: ignore[no-untyped-def]
        conversations, messages = _FakeConversations(), _FakeMessages()
        use_case = AskWidget(
            _FakeLookup([widget]),  # type: ignore[arg-type]
            _FakeQuota(),  # type: ignore[arg-type]
            pipeline,  # type: ignore[arg-type]
            memory=None,
            uow_factory=_uow_factory(conversations, messages),  # type: ignore[arg-type]
            clock=_FixedClock(),
        )
        stream = await use_case.execute(
            AskWidgetCommand(
                widget_id=widget.id,
                knowledge_base_id=widget.knowledge_base_id,
                question="When do you open?",
                session_origin=ORIGIN,
                session_id=uuid4(),
            )
        )
        await _drain(stream)
        return messages


class TestTheWidgetPath(_TestWidgetPath):
    async def test_widget_usage_is_labelled_as_widget(self) -> None:
        pipeline = _CapturingPipeline()
        await self._ask(pipeline, _widget())
        assert pipeline.kwargs["channel"] == "widget"

    async def test_the_stored_answer_carries_its_token_count(self) -> None:
        """It stored 0 for every widget message, so a thread could not show
        what it had cost."""
        messages = await self._ask(_CapturingPipeline(), _widget())
        answer = next(m for m in messages.rows if m.role is MessageRole.ASSISTANT)
        assert answer.token_count == 1_234
