"""The tenant-wide monthly token allowance is enforced on *every* answer path.

The gap these pin: `_assert_within_budget` bounds one **model configuration**,
and is skipped whenever an answer resolves none. Every public-widget answer
uses the platform default and resolves none -- so widget traffic, the one path
with an anonymous stranger on the other end, was completely uncapped on tokens
while the console displayed a limit for it.

The check therefore lives in `answer_from_namespace`, where the authenticated
Ask panel and the public widget both arrive, rather than in `execute`, which
only the authenticated one reaches.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from iam_platform.application.ai_resources.answer_question import AnswerQuestion
from iam_platform.application.ai_resources.exceptions import (
    DailyMessageLimitExceededError,
    TokenBudgetExceededError,
)
from iam_platform.domain.tenancy.entitlements import TenantEntitlements

pytestmark = pytest.mark.asyncio

TENANT = uuid4()


class _Entitlements:
    def __init__(self, limit: int | None) -> None:
        self._limit = limit

    async def get_for_tenant(self, tenant_id: UUID) -> TenantEntitlements:
        now = datetime.now(UTC)
        return TenantEntitlements(
            id=uuid4(),
            tenant_id=tenant_id,
            max_tokens_per_month=self._limit,
            created_at=now,
            updated_at=now,
        )


class _NoChatbotSettings:
    """No stored row: the tenant has expressed no preference, so the platform
    ceiling alone governs."""

    async def get_for_tenant(self, tenant_id: UUID) -> None:
        del tenant_id
        return None


class _Uow:
    def __init__(self, limit: int | None) -> None:
        self.entitlements = _Entitlements(limit)
        self.chatbot_settings = _NoChatbotSettings()

    async def __aenter__(self) -> "_Uow":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


def _uow_factory(limit: int | None):
    def factory(actor_id: UUID, tenant_id: UUID) -> _Uow:
        del actor_id, tenant_id
        return _Uow(limit)

    return factory


class _Quota:
    """Reports usage, or refuses to -- the fail-closed case has its own class
    rather than a flag, so a test reading as "unreadable" cannot be mistaken
    for one reading as "zero used"."""

    def __init__(self, used: int) -> None:
        self._used = used

    async def tokens_used_this_month(self, *, tenant_id: UUID) -> int:
        del tenant_id
        return self._used


class _UnreadableQuota:
    async def tokens_used_this_month(self, *, tenant_id: UUID) -> int:
        del tenant_id
        raise RuntimeError("redis is down")


class _NeverCalledSearch:
    """Retrieval must not happen once the allowance is spent.

    An embedding call and a rerank are both billable, so a check that let them
    run and refused afterwards would still cost the money it exists to save.
    """

    def __init__(self) -> None:
        self.calls = 0

    async def search_chunks(self, **kwargs: object) -> list[object]:
        del kwargs
        self.calls += 1
        return []


class _EmptyReranker:
    """Returns nothing, so an allowed answer stops at "no passages" rather than
    reaching a chat model. These tests are about the allowance gate, not about
    generation -- `search.calls` is what proves the gate let the request past."""

    async def rerank(self, **kwargs: object) -> list[object]:
        del kwargs
        return []


def _pipeline(*, limit: int | None, used: int, unreadable: bool = False):
    search = _NeverCalledSearch()
    pipeline = AnswerQuestion(
        _uow_factory(limit),  # type: ignore[arg-type]
        search,  # type: ignore[arg-type]
        _EmptyReranker(),  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        tenant_quota=_UnreadableQuota() if unreadable else _Quota(used),
    )
    return pipeline, search


class TestTheTenantAllowanceIsEnforcedOnThePublicPath:
    async def test_a_spent_allowance_refuses_before_any_retrieval(self) -> None:
        pipeline, search = _pipeline(limit=1000, used=1000)
        with pytest.raises(TokenBudgetExceededError):
            await pipeline.answer_from_namespace(
                "hello", namespace=f"{TENANT}/{uuid4()}", tenant_id=TENANT
            )
        assert search.calls == 0, "retrieval ran despite the allowance being spent"

    async def test_going_over_the_allowance_also_refuses(self) -> None:
        """A single answer may cross the line -- its cost is unknowable until
        generated -- so the next one must find `used > limit` and refuse."""
        pipeline, _ = _pipeline(limit=1000, used=1501)
        with pytest.raises(TokenBudgetExceededError):
            await pipeline.answer_from_namespace(
                "hello", namespace=f"{TENANT}/{uuid4()}", tenant_id=TENANT
            )

    async def test_an_unreadable_counter_fails_closed(self) -> None:
        """An allowance that cannot be confirmed must not become unlimited --
        that failure is invisible until the invoice."""
        pipeline, search = _pipeline(limit=1000, used=0, unreadable=True)
        with pytest.raises(TokenBudgetExceededError):
            await pipeline.answer_from_namespace(
                "hello", namespace=f"{TENANT}/{uuid4()}", tenant_id=TENANT
            )
        assert search.calls == 0


class TestItDoesNotRefuseWhatItShouldNot:
    """Guards against the enforcement being so eager it breaks ordinary use --
    the failure mode that would be reported as "the chatbot stopped working"."""

    async def test_usage_under_the_allowance_proceeds(self) -> None:
        pipeline, search = _pipeline(limit=1000, used=999)
        await pipeline.answer_from_namespace(
            "hello", namespace=f"{TENANT}/{uuid4()}", tenant_id=TENANT
        )
        assert search.calls == 1

    async def test_no_limit_set_means_uncapped(self) -> None:
        """`None` is uncapped and is deliberately not the same as `0`."""
        pipeline, search = _pipeline(limit=None, used=10_000_000)
        await pipeline.answer_from_namespace(
            "hello", namespace=f"{TENANT}/{uuid4()}", tenant_id=TENANT
        )
        assert search.calls == 1

    async def test_a_zero_limit_really_does_mean_none_at_all(self) -> None:
        pipeline, _ = _pipeline(limit=0, used=0)
        with pytest.raises(TokenBudgetExceededError):
            await pipeline.answer_from_namespace(
                "hello", namespace=f"{TENANT}/{uuid4()}", tenant_id=TENANT
            )

    async def test_without_a_tenant_nothing_is_enforced(self) -> None:
        """Not counted, not capped: the same rule metering uses, so the two
        cannot disagree about which answers are governed."""
        pipeline, search = _pipeline(limit=0, used=999_999)
        await pipeline.answer_from_namespace("hello", namespace=f"{uuid4()}/{uuid4()}")
        assert search.calls == 1


class _MeteringSearch:
    """Charges a fixed embedding cost into whatever meter it is handed.

    Stands in for the real path, where `search_chunks` embeds the question and
    the provider reports what that cost.
    """

    def __init__(self, embedding_cost: int = 40) -> None:
        self.embedding_cost = embedding_cost
        self.saw_meter = False

    async def search_chunks(self, **kwargs: object) -> list[object]:
        usage = kwargs.get("usage")
        if usage is not None:
            self.saw_meter = True
            usage.input_tokens += self.embedding_cost  # type: ignore[union-attr]
            usage.total += self.embedding_cost  # type: ignore[union-attr]
        return []


class _RecordingQuota:
    def __init__(self) -> None:
        self.recorded: list[int] = []

    async def tokens_used_this_month(self, *, tenant_id: UUID) -> int:
        del tenant_id
        return 0

    async def record_tokens(self, *, tenant_id: UUID, usage: object) -> None:
        del tenant_id
        self.recorded.append(usage.total)  # type: ignore[attr-defined]


class TestEmbeddingTokensAreCounted:
    """Every question spends tokens twice -- embedding it, then answering it.

    Metering only the completion understated every usage figure on both
    dashboards by the whole embedding cost, silently and permanently.
    """

    async def test_the_meter_reaches_retrieval(self) -> None:
        search = _MeteringSearch()
        pipeline = AnswerQuestion(
            _uow_factory(None),  # type: ignore[arg-type]
            search,  # type: ignore[arg-type]
            _EmptyReranker(),  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            tenant_quota=_RecordingQuota(),
        )
        await pipeline.answer_from_namespace(
            "hello", namespace=f"{TENANT}/{uuid4()}", tenant_id=TENANT
        )
        assert search.saw_meter, "retrieval was not given a meter to charge"

    async def test_an_unmetered_answer_still_gets_no_meter(self) -> None:
        """No tenant means nothing to attribute the cost to, so the adapter is
        not asked for a figure at all -- exactly as before metering existed."""
        search = _MeteringSearch()
        pipeline = AnswerQuestion(
            _uow_factory(None),  # type: ignore[arg-type]
            search,  # type: ignore[arg-type]
            _EmptyReranker(),  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            tenant_quota=_RecordingQuota(),
        )
        await pipeline.answer_from_namespace("hello", namespace=f"{uuid4()}/{uuid4()}")
        assert not search.saw_meter


class _MessageQuota:
    """Tracks reservations and releases for the daily message allowance."""

    def __init__(self, *, allow: bool = True) -> None:
        self._allow = allow
        self.consumed = 0
        self.released = 0
        self.limits_seen: list[int | None] = []

    async def tokens_used_this_month(self, *, tenant_id: UUID) -> int:
        del tenant_id
        return 0

    async def consume_message(
        self, *, tenant_id: UUID, limit: int | None, **kwargs: object
    ) -> bool:
        del tenant_id
        self.consumed += 1
        self.limits_seen.append(limit)
        return self._allow

    async def release_message(self, *, tenant_id: UUID, **kwargs: object) -> None:
        del tenant_id
        self.released += 1

    async def record_tokens(self, *, tenant_id: UUID, usage: object) -> None:
        del tenant_id, usage


class TestTheAskPanelSpendsFromTheDailyAllowance:
    """It did not, and the omission was invisible in the worst way: a tenant
    working entirely through the console saw "0 messages today" while spending
    real tokens, and their daily cap governed only the visitors they could
    already see in the Inbox.
    """

    async def test_a_question_consumes_one_message(self) -> None:
        quota = _MessageQuota()
        pipeline = AnswerQuestion(
            _uow_factory(None),  # type: ignore[arg-type]
            _NeverCalledSearch(),  # type: ignore[arg-type]
            _EmptyReranker(),  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            tenant_quota=quota,
        )
        # Returns the zone it reserved under, so the matching release can use
        # the identical key -- `None` would mean nothing was reserved.
        assert await pipeline._consume_daily_message(TENANT) is not None
        assert quota.consumed == 1

    async def test_being_over_the_limit_refuses(self) -> None:
        quota = _MessageQuota(allow=False)
        pipeline = AnswerQuestion(
            _uow_factory(None),  # type: ignore[arg-type]
            _NeverCalledSearch(),  # type: ignore[arg-type]
            _EmptyReranker(),  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            tenant_quota=quota,
        )
        with pytest.raises(DailyMessageLimitExceededError):
            await pipeline._consume_daily_message(TENANT)

    async def test_a_failed_answer_releases_the_reservation(self) -> None:
        quota = _MessageQuota()
        pipeline = AnswerQuestion(
            _uow_factory(None),  # type: ignore[arg-type]
            _NeverCalledSearch(),  # type: ignore[arg-type]
            _EmptyReranker(),  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            tenant_quota=quota,
        )
        await pipeline._release_daily_message(TENANT, UTC)
        assert quota.released == 1

    async def test_without_a_tenant_nothing_is_consumed(self) -> None:
        """Not counted, not capped -- the same rule metering uses, so the two
        cannot disagree about which answers are governed."""
        quota = _MessageQuota()
        pipeline = AnswerQuestion(
            _uow_factory(None),  # type: ignore[arg-type]
            _NeverCalledSearch(),  # type: ignore[arg-type]
            _EmptyReranker(),  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            tenant_quota=quota,
        )
        assert await pipeline._consume_daily_message(None) is None
        assert quota.consumed == 0
