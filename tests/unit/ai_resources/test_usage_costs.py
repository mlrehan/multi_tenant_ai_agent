"""Estimated AI cost: recorded facts, entered prices, and honest totals.

The per-row pricing runs in SQL and was proven against Postgres (a hand
calculation of $0.0084192 matched to the last digit, and a later price left an
earlier row's cost untouched). These tests cover everything around it: that
the facts a cost needs are recorded at all, that prices are validated and
gated, and that the totals never turn "unpriced" into "free".
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

from iam_platform.application.ai_resources import usage_costs
from iam_platform.application.ai_resources.cost_summary import current_price, summarise_costs
from iam_platform.application.ai_resources.exceptions import (
    ModelConfigurationManagementDeniedError,
    ModelPriceConflictError,
    ModelPriceNotFoundError,
)
from iam_platform.application.ai_resources.ports import (
    INGESTION_CHANNEL,
    TokenUsage,
    UsageCostLine,
    UsageEvent,
)
from iam_platform.core.clock import FixedClock
from iam_platform.core.config import OpenAISettings
from iam_platform.domain.ai_resources.pricing import ModelPrice, ModelPriceInvalid, cost_of
from iam_platform.infrastructure.db.repositories.platform_activity import (
    SqlPlatformActivityReader,
)
from iam_platform.infrastructure.db.usage_ledger import SqlUsageLedger
from iam_platform.infrastructure.embeddings.openai_client import OpenAIEmbeddingClient
from iam_platform.workers.jobs.indexing import record_ingestion_usage

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 28, 12, tzinfo=UTC)
MONTH = datetime(2026, 9, 1, tzinfo=UTC)
A, B = uuid4(), uuid4()
MANAGE = "platform.model_configurations.manage"


def _price(model: str, inp: str, out: str = "0", at: datetime = MONTH) -> ModelPrice:
    return ModelPrice.validated(
        id=uuid4(),
        model_name=model,
        input_usd_per_million=Decimal(inp),
        output_usd_per_million=Decimal(out),
        effective_from=at,
        created_by_user_id=None,
        created_at=at,
    )


# -- prices -------------------------------------------------------------------


class TestAPriceIsValidated:
    def test_a_real_looking_price_is_accepted_and_trimmed(self) -> None:
        price = _price("  gpt-5.5 ", "1.25", "10")
        assert price.model_name == "gpt-5.5"
        assert price.input_usd_per_million == Decimal("1.25")

    @pytest.mark.parametrize(
        ("inp", "reason"),
        [
            ("-0.01", "zero or more"),
            ("0.0000001", "6 decimal places"),
            ("10000.01", "misplaced decimal point"),
            ("NaN", "zero or more"),
        ],
    )
    def test_an_impossible_price_is_refused(self, inp: str, reason: str) -> None:
        with pytest.raises(ModelPriceInvalid, match=reason):
            _price("gpt-5.5", inp)

    def test_a_blank_model_name_is_refused(self) -> None:
        with pytest.raises(ModelPriceInvalid):
            _price("   ", "1")

    def test_a_naive_effective_time_is_refused(self) -> None:
        with pytest.raises(ModelPriceInvalid, match="time zone"):
            _price("gpt-5.5", "1", at=datetime(2026, 9, 1))

    def test_cost_is_exact_decimal_arithmetic(self) -> None:
        assert cost_of(6_469, Decimal("1")) + cost_of(125, Decimal("10")) == Decimal("0.007719")


class TestThePriceInForce:
    def test_the_latest_entry_at_or_before_the_moment_wins(self) -> None:
        early, later = _price("m", "1"), _price("m", "100", at=MONTH + timedelta(days=20))
        prices = [later, early]
        assert current_price(prices, "m", MONTH + timedelta(days=5)) is early
        assert current_price(prices, "m", MONTH + timedelta(days=21)) is later

    def test_no_entry_yet_means_no_price_not_zero(self) -> None:
        assert current_price([_price("m", "1")], "m", MONTH - timedelta(seconds=1)) is None
        assert current_price([_price("m", "1")], "other", NOW) is None


# -- totals -------------------------------------------------------------------


def _line(tenant: UUID, kind: str, model: str | None, tokens: int, unpriced: int, cost: str) -> UsageCostLine:
    return UsageCostLine(
        tenant_id=tenant, kind=kind, model=model, tokens=tokens,
        unpriced_tokens=unpriced, cost_usd=Decimal(cost),
    )


class TestTheSummary:
    def test_unpriced_is_counted_and_never_costed(self) -> None:
        summary = summarise_costs([
            _line(A, "chat", "gpt-5.5", 6_594, 0, "0.007719"),
            _line(A, "embedding", "text-embedding-3-large", 7_002, 0, "0.0007002"),
            _line(A, "chat", None, 39_445, 39_445, "0"),
            _line(B, "embedding", None, 13_988, 13_988, "0"),
        ])
        assert summary.total_usd == Decimal("0.0084192")
        assert (summary.priced_tokens, summary.unpriced_tokens) == (13_596, 53_433)
        assert summary.by_tenant[A].cost_usd == Decimal("0.0084192")
        assert summary.by_tenant[B].unpriced_tokens == 13_988
        # Priced models first, most expensive first; wholly unpriced last.
        assert [m.model for m in summary.by_model[:2]] == ["gpt-5.5", "text-embedding-3-large"]
        assert all(m.unpriced_tokens == m.tokens for m in summary.by_model[2:])

    def test_one_model_across_tenants_is_one_row(self) -> None:
        summary = summarise_costs([
            _line(A, "chat", "gpt-5.5", 100, 0, "0.1"),
            _line(B, "chat", "gpt-5.5", 300, 0, "0.3"),
        ])
        (row,) = summary.by_model
        assert (row.tokens, row.cost_usd) == (400, Decimal("0.4"))


# -- the price-list use cases ---------------------------------------------------


@dataclass
class _Prices:
    rows: list[ModelPrice] = field(default_factory=list)

    async def list_all(self) -> list[ModelPrice]:
        return list(self.rows)

    async def get(self, price_id: UUID) -> ModelPrice | None:
        return next((p for p in self.rows if p.id == price_id), None)

    async def exists(self, *, model_name: str, effective_from: datetime) -> bool:
        return any(p.model_name == model_name and p.effective_from == effective_from for p in self.rows)

    async def add(self, price: ModelPrice) -> None:
        self.rows.append(price)

    async def delete(self, price_id: UUID) -> None:
        self.rows = [p for p in self.rows if p.id != price_id]


class _Activity:
    def __init__(self, lines: list[UsageCostLine]) -> None:
        self.lines = lines
        self.since: datetime | None = None

    async def usage_cost_lines(self, *, since: datetime) -> list[UsageCostLine]:
        self.since = since
        return self.lines


class _Uow:
    def __init__(self, prices: _Prices, activity: _Activity | None = None) -> None:
        self.model_prices = prices
        self.activity = activity or _Activity([])
        self.audited: list[dict[str, Any]] = []
        self.audit = self

    async def record(self, **kwargs: Any) -> None:
        self.audited.append(kwargs)

    async def __aenter__(self) -> _Uow:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


def _grant(monkeypatch: pytest.MonkeyPatch, permissions: set[str]) -> None:
    async def fake_state(uow: object, actor: object, *, now: object) -> object:
        return SimpleNamespace(permissions=permissions)

    monkeypatch.setattr(usage_costs, "compute_effective_platform_state", fake_state)


def _set(uow: _Uow, **overrides: Any) -> Any:
    command: dict[str, Any] = {
        "actor_user_id": str(uuid4()),
        "model_name": "gpt-5.5",
        "input_usd_per_million": Decimal("1.25"),
        "output_usd_per_million": Decimal("10"),
    }
    command.update(overrides)
    return usage_costs.SetModelPrice(lambda _a: uow, FixedClock(NOW)).execute(  # type: ignore[arg-type,return-value]
        usage_costs.SetModelPriceCommand(**command)
    )


class TestSettingAPrice:
    async def test_it_is_stored_from_now_and_audited(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _grant(monkeypatch, {MANAGE})
        uow = _Uow(_Prices())
        await _set(uow)
        (price,) = uow.model_prices.rows
        assert price.effective_from == NOW
        assert uow.audited[0]["action"] == "platform.model_price_created"
        assert uow.audited[0]["metadata"]["input_usd_per_million"] == "1.25"

    async def test_backdating_to_price_usage_already_recorded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _grant(monkeypatch, {MANAGE})
        uow = _Uow(_Prices())
        await _set(uow, effective_from=MONTH)
        assert uow.model_prices.rows[0].effective_from == MONTH

    async def test_without_the_operator_permission_nothing_is_written(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _grant(monkeypatch, set())
        uow = _Uow(_Prices())
        with pytest.raises(ModelConfigurationManagementDeniedError):
            await _set(uow)
        assert uow.model_prices.rows == [] and uow.audited == []

    async def test_a_second_entry_for_the_same_moment_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _grant(monkeypatch, {MANAGE})
        uow = _Uow(_Prices([_price("gpt-5.5", "1", at=MONTH)]))
        with pytest.raises(ModelPriceConflictError):
            await _set(uow, effective_from=MONTH)
        assert len(uow.model_prices.rows) == 1


class TestDeletingAPrice:
    async def test_it_is_removed_and_the_values_it_held_are_audited(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _grant(monkeypatch, {MANAGE})
        price = _price("gpt-5.5", "1.25", "10")
        uow = _Uow(_Prices([price]))
        await usage_costs.DeleteModelPrice(lambda _a: uow, FixedClock(NOW)).execute(  # type: ignore[arg-type,return-value]
            usage_costs.DeleteModelPriceCommand(actor_user_id=str(uuid4()), price_id=str(price.id))
        )
        assert uow.model_prices.rows == []
        assert uow.audited[0]["action"] == "platform.model_price_deleted"
        assert uow.audited[0]["metadata"]["output_usd_per_million"] == "10"

    async def test_an_unknown_id_is_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _grant(monkeypatch, {MANAGE})
        with pytest.raises(ModelPriceNotFoundError):
            await usage_costs.DeleteModelPrice(lambda _a: _Uow(_Prices()), FixedClock(NOW)).execute(  # type: ignore[arg-type,return-value]
                usage_costs.DeleteModelPriceCommand(actor_user_id=str(uuid4()), price_id=str(uuid4()))
            )


class TestTheCatalogue:
    async def test_models_in_use_this_month_show_their_current_price(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _grant(monkeypatch, {MANAGE})
        activity = _Activity([
            _line(A, "chat", "gpt-5.5", 6_594, 0, "0.007719"),
            _line(B, "chat", "gpt-5.5", 406, 0, "0.0005"),
            _line(A, "embedding", "text-embedding-3-large", 7_002, 7_002, "0"),
            _line(A, "chat", None, 39_445, 39_445, "0"),
        ])
        uow = _Uow(_Prices([_price("gpt-5.5", "1")]), activity)
        catalogue = await usage_costs.ListModelPrices(lambda _a: uow, FixedClock(NOW)).execute(  # type: ignore[arg-type,return-value]
            usage_costs.ListModelPricesQuery(actor_user_id=str(uuid4()))
        )
        assert activity.since == MONTH
        by_model = {m.model: m for m in catalogue.models_in_use}
        assert set(by_model) == {"gpt-5.5", "text-embedding-3-large"}  # no nameless row
        assert by_model["gpt-5.5"].tokens_this_month == 7_000
        assert by_model["gpt-5.5"].current is not None
        assert by_model["text-embedding-3-large"].current is None


# -- the facts a cost needs are recorded ----------------------------------------


@dataclass
class _Usage:
    prompt_tokens: int


@dataclass
class _Item:
    index: int
    embedding: list[float]


@dataclass
class _Response:
    data: list[_Item]
    usage: _Usage


class _Embeddings:
    async def create(self, *, model: str, input: list[str], dimensions: int) -> _Response:
        return _Response(
            data=[_Item(index=i, embedding=[0.0] * dimensions) for i in range(len(input))],
            usage=_Usage(prompt_tokens=8),
        )


class TestTheAdaptersRecordWhatTheyCalled:
    async def test_the_embedding_is_separable_and_names_its_model(self) -> None:
        client = OpenAIEmbeddingClient(
            OpenAISettings(embedding_dimensions=4, embedding_model="text-embedding-3-large"),
            client=SimpleNamespace(embeddings=_Embeddings()),
        )
        meter = TokenUsage()
        await client.embed("What discounts do you offer?", usage=meter)
        assert (meter.input_tokens, meter.embedding_tokens, meter.total) == (8, 8, 8)
        assert meter.embedding_model == "text-embedding-3-large"

    async def test_the_chat_model_named_is_the_one_sent(self) -> None:
        from tests.unit.ai_resources.test_chat_and_reranking import (
            OpenAIChatModel,
            _context,
            _Event,
            _FakeOpenAI,
            _UsageEvent,
        )
        from tests.unit.ai_resources.test_chat_and_reranking import (
            _Usage as _ChatUsage,
        )

        for override, expected in ((None, "gpt-default"), ("gpt-override", "gpt-override")):
            client = _FakeOpenAI()

            async def stream(**kwargs: Any) -> AsyncIterator[object]:
                async def events() -> AsyncIterator[object]:
                    yield _Event("Hi")
                    yield _UsageEvent(_ChatUsage(prompt=10, completion=2, total=12))

                return events()

            client.completions.create = stream  # type: ignore[method-assign]
            meter = TokenUsage()
            model = OpenAIChatModel(OpenAISettings(chat_model="gpt-default"), client=client)
            [t async for t in model.stream_answer(
                question="q", context=_context(), system_prompt="s", usage=meter,
                model_name=override,
            )]
            assert meter.chat_model == expected


class _ModelNamingChat:
    """Fills the meter as the real adapters do, models included."""

    async def _stream(self, usage: TokenUsage | None) -> AsyncIterator[str]:
        yield "We open at 9 [1]."
        if usage is not None:
            usage.input_tokens += 900
            usage.output_tokens += 100
            usage.total += 1_000
            usage.embedding_tokens += 7
            usage.embedding_model = "text-embedding-3-large"
            usage.chat_model = "gpt-5.5"

    def stream_answer(self, *, usage: TokenUsage | None = None, **kwargs: object) -> AsyncIterator[str]:
        del kwargs
        return self._stream(usage)


class TestTheLedgerRowCarriesThem:
    async def test_an_answer_row_names_its_models_and_embedding_share(self) -> None:
        from tests.unit.ai_resources.test_answer_question import _chunk
        from tests.unit.ai_resources.test_usage_ledger import TENANT, _drain, _Events, _pipeline

        events = _Events()
        pipeline = _pipeline(events, [_chunk("We open at 9.")])
        pipeline._chat_model = _ModelNamingChat()  # type: ignore[assignment]
        stream = await pipeline.answer_from_namespace(
            "When do you open?", namespace=f"{TENANT}/{uuid4()}", tenant_id=TENANT
        )
        await _drain(stream)
        (row,) = events.rows
        assert (row.chat_model, row.embedding_model, row.embedding_tokens) == (
            "gpt-5.5", "text-embedding-3-large", 7,
        )

    async def test_an_ingestion_row_is_all_embedding(self) -> None:
        recorded: list[UsageEvent] = []

        class _Ledger:
            async def record(self, event: UsageEvent) -> None:
                recorded.append(event)

        usage = TokenUsage(input_tokens=6_994, total=6_994, embedding_model="text-embedding-3-large")
        await record_ingestion_usage(_Ledger(), tenant_id=A, usage=usage, occurred_at=NOW)  # type: ignore[arg-type]
        (row,) = recorded
        assert row.channel == INGESTION_CHANNEL
        assert (row.embedding_tokens, row.embedding_model) == (6_994, "text-embedding-3-large")

    async def test_the_insert_writes_the_new_columns(self) -> None:
        from tests.unit.ai_resources.test_ingestion_metering import _CapturingSession

        sent: list[str] = []
        await SqlUsageLedger(lambda: _CapturingSession(sent)).record(  # type: ignore[arg-type,return-value]
            UsageEvent(
                tenant_id=A, channel="widget", model_configuration_id=None,
                input_tokens=6_477, output_tokens=125, total_tokens=6_602, occurred_at=NOW,
                embedding_tokens=8, chat_model="gpt-5.5", embedding_model="text-embedding-3-large",
            )
        )
        (insert,) = [s for s in sent if s.startswith("INSERT INTO ai_usage_events")]
        assert "'gpt-5.5'" in insert and "'text-embedding-3-large'" in insert
        assert "embedding_tokens" in insert


class TestThePricedQuery:
    """The SQL itself is proven live; these pin the rules a refactor could
    quietly drop."""

    async def _sql(self) -> str:
        captured: list[str] = []

        class _Session:
            async def execute(self, statement: Any, params: Any = None) -> Any:
                captured.append(str(statement))
                return SimpleNamespace(all=lambda: [])

        await SqlPlatformActivityReader(_Session()).usage_cost_lines(since=MONTH)  # type: ignore[arg-type]
        return " ".join(captured[0].split())

    async def test_each_row_uses_the_price_in_force_when_it_occurred(self) -> None:
        sql = await self._sql()
        assert sql.count("effective_from <= u.occurred_at ORDER BY effective_from DESC LIMIT 1") == 2

    async def test_a_row_without_a_price_is_unpriced_not_free(self) -> None:
        sql = await self._sql()
        assert "FILTER (WHERE NOT priced)" in sql
        # Both halves: an inner join would drop unpriced rows from the totals entirely.
        assert sql.count("LEFT JOIN LATERAL") == 2
