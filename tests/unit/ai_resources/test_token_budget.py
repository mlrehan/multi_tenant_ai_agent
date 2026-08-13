"""`token_budget_per_month`, enforced.

The field existed from the start: platform admin could type a number, the API
returned it, the console displayed it — and nothing anywhere read it back. A
number that looks like a spending control and is not one is worse than no
number, because an operator sets it and believes they are protected. These
tests are what make it real, so each one states the failure it prevents rather
than the code path it covers.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from iam_platform.application.ai_resources.exceptions import TokenBudgetExceededError
from iam_platform.domain.ai_resources.entities import (
    AiAssistant,
    AssistantStatus,
    ModelConfiguration,
    ResourceVisibility,
)
from tests.unit.ai_resources.fakes import FakeAiResourceUnitOfWork
from tests.unit.ai_resources.test_answer_question import (
    _chunk,
    _FakeChatModel,
    _FakeVectorSearch,
)
from tests.unit.ai_resources.test_answer_question_cases import _build, _query, _seed

pytestmark = pytest.mark.unit

NOW = datetime(2026, 1, 1, tzinfo=UTC)


class _FakeTokenUsage:
    """A usage store whose numbers the test controls.

    `reads` and `records` are kept so a test can assert the store was actually
    *consulted*. "Did not refuse" and "never checked" produce the same answer
    and only one of them is a working budget.
    """

    def __init__(self, *, spent: int = 0, read_fails: bool = False) -> None:
        self._spent = spent
        self._read_fails = read_fails
        self.reads: list[tuple[UUID, UUID]] = []
        self.records: list[tuple[UUID, UUID, int]] = []

    async def read(self, *, tenant_id: UUID, model_configuration_id: UUID) -> int:
        self.reads.append((tenant_id, model_configuration_id))
        if self._read_fails:
            raise RuntimeError("redis is down")
        return self._spent

    async def record(
        self, *, tenant_id: UUID, model_configuration_id: UUID, tokens: int
    ) -> None:
        self.records.append((tenant_id, model_configuration_id, tokens))
        self._spent += tokens


class _UsageReportingChatModel(_FakeChatModel):
    """Reports cost the way the real adapter does: by filling in the caller's
    `usage` object rather than returning a value, because the number only
    exists once the stream has finished."""

    def __init__(self, *, cost: int, reply: str = "An answer [1].") -> None:
        super().__init__(reply=reply)
        self._cost = cost
        self.usage_requested: list[bool] = []

    def stream_answer(self, **kwargs: object):  # type: ignore[no-untyped-def,override]
        usage = kwargs.pop("usage", None)
        self.usage_requested.append(usage is not None)
        if usage is not None:
            usage.total = self._cost  # type: ignore[attr-defined]
        return super().stream_answer(**kwargs)  # type: ignore[arg-type]


def _seed_budgeted_assistant(
    uow: FakeAiResourceUnitOfWork,
    *,
    tenant_id: UUID,
    membership_id: UUID,
    token_budget_per_month: int | None,
) -> AiAssistant:
    configuration = ModelConfiguration(
        id=uuid4(),
        tenant_id=None,
        model_name="gpt-5.5",
        parameters={},
        token_budget_per_month=token_budget_per_month,
        created_at=NOW,
        updated_at=NOW,
    )
    uow.model_configurations.by_id[configuration.id] = configuration
    uow.model_configurations.grant(
        tenant_id=tenant_id, model_configuration_id=configuration.id
    )
    assistant = AiAssistant(
        id=uuid4(),
        tenant_id=tenant_id,
        name="Support Bot",
        owner_membership_id=membership_id,
        visibility=ResourceVisibility.TENANT,
        model_configuration_id=configuration.id,
        status=AssistantStatus.PUBLISHED,
        created_at=NOW,
        updated_at=NOW,
    )
    uow.assistants.by_id[assistant.id] = assistant
    return assistant


def _membership_id(uow: FakeAiResourceUnitOfWork) -> UUID:
    return next(iter(uow.tenant_memberships.by_id))


class TestTokenBudget:
    async def test_an_answer_within_budget_records_what_it_cost(self) -> None:
        uow = FakeAiResourceUnitOfWork()
        tenant_id, user_id, kb = _seed(uow)
        assistant = _seed_budgeted_assistant(
            uow,
            tenant_id=tenant_id,
            membership_id=_membership_id(uow),
            token_budget_per_month=1000,
        )
        usage = _FakeTokenUsage(spent=10)
        chat = _UsageReportingChatModel(cost=42)
        use_case = _build(uow, _FakeVectorSearch(chunks=[_chunk("x")]), chat, usage)

        result = await use_case.execute(
            _query(tenant_id, user_id, kb, "Refunds?", assistant_id=str(assistant.id))
        )
        [token async for token in result.tokens]

        assert usage.reads, "the budget must be consulted, not assumed"
        assert [tokens for _t, _m, tokens in usage.records] == [42]

    async def test_a_spent_budget_refuses_before_the_model_is_called(self) -> None:
        """Refused *before* generation — the only moment at which refusing
        saves anything. Checking afterwards would merely record that the money
        had already been spent."""
        uow = FakeAiResourceUnitOfWork()
        tenant_id, user_id, kb = _seed(uow)
        assistant = _seed_budgeted_assistant(
            uow,
            tenant_id=tenant_id,
            membership_id=_membership_id(uow),
            token_budget_per_month=1000,
        )
        chat = _UsageReportingChatModel(cost=42)
        use_case = _build(
            uow, _FakeVectorSearch(chunks=[_chunk("x")]), chat, _FakeTokenUsage(spent=1000)
        )

        with pytest.raises(TokenBudgetExceededError):
            await use_case.execute(
                _query(tenant_id, user_id, kb, "Refunds?", assistant_id=str(assistant.id))
            )

        assert chat.calls == [], "the model must not be called once the budget is spent"

    async def test_an_unreadable_counter_refuses_rather_than_allowing_unlimited_spend(
        self,
    ) -> None:
        """Fails closed. A budget that cannot be confirmed quietly becoming an
        unlimited one is the exact failure this counter exists to prevent, and
        it stays invisible until the invoice arrives."""
        uow = FakeAiResourceUnitOfWork()
        tenant_id, user_id, kb = _seed(uow)
        assistant = _seed_budgeted_assistant(
            uow,
            tenant_id=tenant_id,
            membership_id=_membership_id(uow),
            token_budget_per_month=1000,
        )
        chat = _UsageReportingChatModel(cost=42)
        use_case = _build(
            uow,
            _FakeVectorSearch(chunks=[_chunk("x")]),
            chat,
            _FakeTokenUsage(read_fails=True),
        )

        with pytest.raises(TokenBudgetExceededError):
            await use_case.execute(
                _query(tenant_id, user_id, kb, "Refunds?", assistant_id=str(assistant.id))
            )

        assert chat.calls == []

    async def test_no_budget_set_means_unlimited_and_is_not_even_checked(self) -> None:
        """`None` is the default on every configuration created so far, so this
        is the path essentially every existing deployment is on. It must not
        acquire a limit by accident."""
        uow = FakeAiResourceUnitOfWork()
        tenant_id, user_id, kb = _seed(uow)
        assistant = _seed_budgeted_assistant(
            uow,
            tenant_id=tenant_id,
            membership_id=_membership_id(uow),
            token_budget_per_month=None,
        )
        usage = _FakeTokenUsage(spent=10**9)
        chat = _UsageReportingChatModel(cost=42)
        use_case = _build(uow, _FakeVectorSearch(chunks=[_chunk("x")]), chat, usage)

        result = await use_case.execute(
            _query(tenant_id, user_id, kb, "Refunds?", assistant_id=str(assistant.id))
        )
        [token async for token in result.tokens]

        assert usage.reads == [], "an unlimited configuration must not consult the counter"
        # Still *metered*, though: spend is worth knowing where it is not
        # capped, and it is what the platform console reports.
        assert [tokens for _t, _m, tokens in usage.records] == [42]

    async def test_a_platform_default_answer_is_not_metered(self) -> None:
        """No assistant means no model configuration, and the budget lives on a
        configuration row — so there is nothing to attribute spend to. Asserted
        plainly here rather than left for someone to discover as a gap."""
        uow = FakeAiResourceUnitOfWork()
        tenant_id, user_id, kb = _seed(uow)
        usage = _FakeTokenUsage()
        chat = _UsageReportingChatModel(cost=42)
        use_case = _build(uow, _FakeVectorSearch(chunks=[_chunk("x")]), chat, usage)

        result = await use_case.execute(_query(tenant_id, user_id, kb, "Refunds?"))
        [token async for token in result.tokens]

        assert usage.reads == [] and usage.records == []
        # And the adapter is not asked for a figure nobody will read, so the
        # request sent upstream is the one that was always sent.
        assert chat.usage_requested == [False]

    async def test_an_abandoned_answer_is_still_billed(self) -> None:
        """The provider charges for tokens it generated whether or not anyone
        read them. A counter that only recorded fully-consumed streams would be
        avoidable by disconnecting early."""
        uow = FakeAiResourceUnitOfWork()
        tenant_id, user_id, kb = _seed(uow)
        assistant = _seed_budgeted_assistant(
            uow,
            tenant_id=tenant_id,
            membership_id=_membership_id(uow),
            token_budget_per_month=1000,
        )
        usage = _FakeTokenUsage()
        chat = _UsageReportingChatModel(cost=42, reply="one two three four five")
        use_case = _build(uow, _FakeVectorSearch(chunks=[_chunk("x")]), chat, usage)

        result = await use_case.execute(
            _query(tenant_id, user_id, kb, "Refunds?", assistant_id=str(assistant.id))
        )
        # One token, then walk away. `aclose()` is what a disconnected HTTP
        # client ultimately triggers on the generator.
        agen = result.tokens
        await agen.__anext__()
        await agen.aclose()  # type: ignore[attr-defined]

        assert [tokens for _t, _m, tokens in usage.records] == [42]
