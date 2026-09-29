"""What the platform is *doing*: activity, satisfaction, queues and ingestion.

The companion to `platform_overview.py`, which answers "what is being spent".
Together they are the operator dashboard. Split because the two have
different shapes -- spend is per tenant against an allowance, activity is a
time series and a set of open problems -- and one use case holding both would
be two unrelated reads behind one permission check.

**Comparisons are computed here, not in the console.** "Questions this week,
up 12% on last week" is a claim, and a claim made by the client is one each
client can make differently. The series is zero-filled for the same reason: a
quiet day is a 0 on the chart, not a missing bar the chart library silently
closes the gap over.

**Counts only.** Nothing returned names a question, a document or a visitor,
so -- unlike the feedback review, which shows what people typed -- reading
this is not audited.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID

from iam_platform.application.ai_resources.exceptions import (
    ModelConfigurationManagementDeniedError,
)
from iam_platform.application.ai_resources.platform_overview import MANAGE_PERMISSION
from iam_platform.application.ai_resources.ports import (
    DailyActivityCounts,
    TenantAttentionCounts,
)
from iam_platform.application.platform_authz.effective_permissions import (
    compute_effective_platform_state,
)
from iam_platform.application.platform_authz.ports import PlatformUowFactory
from iam_platform.core.clock import Clock

#: Two weeks, so "the last 7 days" always has a full previous 7 to compare to.
WINDOW_DAYS = 14
TREND_DAYS = 7
SATISFACTION_DAYS = 30
#: A document still `processing` after this long is treated as stuck. Real
#: ingestion of even a 40-page scan finishes in minutes; half an hour means a
#: worker is down or a job was lost, which is what the operator needs to know.
STUCK_AFTER = timedelta(minutes=30)


@dataclass(frozen=True, slots=True)
class PeriodComparison:
    current: int
    previous: int


@dataclass(frozen=True, slots=True)
class PlatformActivity:
    #: When these numbers were read -- a dashboard without an "as of" cannot
    #: be told apart from one that stopped refreshing an hour ago.
    generated_at: datetime
    #: Oldest first, exactly WINDOW_DAYS long, the last entry being today (UTC).
    daily: list[DailyActivityCounts]
    questions: PeriodComparison
    conversations: PeriodComparison
    handoffs: PeriodComparison
    #: Ratings in the last SATISFACTION_DAYS, and the SATISFACTION_DAYS before.
    helpful: PeriodComparison
    not_helpful: PeriodComparison
    active_tenants: int
    waiting_handoffs: int
    handled_handoffs: int
    oldest_waiting_at: datetime | None
    documents_processing: int
    documents_stuck: int
    documents_failed: int
    attention: list[TenantAttentionCounts]
    #: Token figures before this instant do not exist (the ledger began then).
    usage_recorded_since: datetime | None


@dataclass(frozen=True, slots=True)
class PlatformActivityQuery:
    actor_user_id: str


def zero_filled(
    rows: list[DailyActivityCounts], *, today: date, days: int = WINDOW_DAYS
) -> list[DailyActivityCounts]:
    by_day = {r.day: r for r in rows}
    return [
        by_day.get(d)
        or DailyActivityCounts(
            day=d, conversations_started=0, questions=0, answers=0, handoffs=0, tokens=0
        )
        for d in (today - timedelta(days=offset) for offset in range(days - 1, -1, -1))
    ]


def compare(daily: list[DailyActivityCounts], field: str) -> PeriodComparison:
    """The last TREND_DAYS against the TREND_DAYS before them."""
    values = [int(getattr(d, field)) for d in daily]
    return PeriodComparison(
        current=sum(values[-TREND_DAYS:]),
        previous=sum(values[-2 * TREND_DAYS : -TREND_DAYS]),
    )


class GetPlatformActivity:
    """Gated like the rest of the operator dashboard -- see `GetPlatformOverview`."""

    def __init__(self, uow_factory: PlatformUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, query: PlatformActivityQuery) -> PlatformActivity:
        actor_id = UUID(query.actor_user_id)
        now = self._clock.now().astimezone(UTC)
        today = now.date()
        window_start = datetime.combine(
            today - timedelta(days=WINDOW_DAYS - 1), time.min, tzinfo=UTC
        )
        satisfaction_start = now - timedelta(days=SATISFACTION_DAYS)
        stuck_before = now - STUCK_AFTER

        async with self._uow_factory(actor_id) as uow:
            state = await compute_effective_platform_state(uow, actor_id, now=now)
            if MANAGE_PERMISSION not in state.permissions:
                raise ModelConfigurationManagementDeniedError(MANAGE_PERMISSION)

            reader = uow.activity
            daily = zero_filled(await reader.daily_counts(since=window_start), today=today)
            helpful, not_helpful = await reader.feedback_counts(since=satisfaction_start, until=now)
            prev_helpful, prev_not_helpful = await reader.feedback_counts(
                since=satisfaction_start - timedelta(days=SATISFACTION_DAYS),
                until=satisfaction_start,
            )
            active = await reader.active_tenants(since=now - timedelta(days=TREND_DAYS))
            waiting, handled, oldest = await reader.handoff_queue()
            processing, stuck, failed = await reader.document_health(stuck_before=stuck_before)
            attention = await reader.attention(stuck_before=stuck_before)
            recorded_since = await reader.usage_recorded_since()

        return PlatformActivity(
            generated_at=now,
            daily=daily,
            questions=compare(daily, "questions"),
            conversations=compare(daily, "conversations_started"),
            handoffs=compare(daily, "handoffs"),
            helpful=PeriodComparison(current=helpful, previous=prev_helpful),
            not_helpful=PeriodComparison(current=not_helpful, previous=prev_not_helpful),
            active_tenants=active,
            waiting_handoffs=waiting,
            handled_handoffs=handled,
            oldest_waiting_at=oldest,
            documents_processing=processing,
            documents_stuck=stuck,
            documents_failed=failed,
            attention=attention,
            usage_recorded_since=recorded_since,
        )
