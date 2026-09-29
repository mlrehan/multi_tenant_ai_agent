"""What a tenant's own chatbot has been doing -- the tenant dashboard's read.

The tenant-scoped sibling of `platform_activity.py`, built from the same reader
(constructed with this tenant's id, so every query is fenced twice: RLS and an
explicit filter) and the same `zero_filled`/`compare` helpers, so the platform
and the tenant see a week computed identically. A tenant admin and the
operator looking at the same tenant must never be shown two different
"questions this week".

**Counts only**, like its platform sibling: nothing returned names a visitor,
a question or a document, so reading it is not audited. It is still gated on
`tenant.conversations.view` -- the permission that already covers reading
conversations and ratings -- because satisfaction and question volumes are
business information a basic member was never shown.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from uuid import UUID

from iam_platform.application.ai_resources.exceptions import PermissionDeniedError
from iam_platform.application.ai_resources.platform_activity import (
    SATISFACTION_DAYS,
    STUCK_AFTER,
    TREND_DAYS,
    WINDOW_DAYS,
    PeriodComparison,
    compare,
    zero_filled,
)
from iam_platform.application.ai_resources.platform_overview import month_start_utc
from iam_platform.application.ai_resources.ports import (
    AiResourceUowFactory,
    DailyActivityCounts,
    KnowledgeSummary,
)
from iam_platform.core.clock import Clock

VIEW_ACTIVITY_PERMISSION = "tenant.conversations.view"


@dataclass(frozen=True, slots=True)
class TenantActivity:
    generated_at: datetime
    daily: list[DailyActivityCounts]
    questions: PeriodComparison
    conversations: PeriodComparison
    handoffs: PeriodComparison
    helpful: PeriodComparison
    not_helpful: PeriodComparison
    waiting_handoffs: int
    handled_handoffs: int
    oldest_waiting_at: datetime | None
    documents_stuck: int
    knowledge: KnowledgeSummary
    #: Embedding tokens spent reading this tenant's documents this UTC month.
    #: Metered, but not part of the chat allowance.
    ingestion_tokens_this_month: int = 0


@dataclass(frozen=True, slots=True)
class TenantActivityQuery:
    actor_user_id: str
    tenant_id: str
    permissions: frozenset[str]


class GetTenantActivity:
    def __init__(self, uow_factory: AiResourceUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, query: TenantActivityQuery) -> TenantActivity:
        if VIEW_ACTIVITY_PERMISSION not in query.permissions:
            raise PermissionDeniedError(VIEW_ACTIVITY_PERMISSION)

        actor_id = UUID(query.actor_user_id)
        tenant_id = UUID(query.tenant_id)
        now = self._clock.now().astimezone(UTC)
        today = now.date()
        window_start = datetime.combine(
            today - timedelta(days=WINDOW_DAYS - 1), time.min, tzinfo=UTC
        )
        satisfaction_start = now - timedelta(days=SATISFACTION_DAYS)

        async with self._uow_factory(actor_id, tenant_id) as uow:
            reader = uow.activity
            daily = zero_filled(await reader.daily_counts(since=window_start), today=today)
            helpful, not_helpful = await reader.feedback_counts(since=satisfaction_start, until=now)
            prev_helpful, prev_not_helpful = await reader.feedback_counts(
                since=satisfaction_start - timedelta(days=SATISFACTION_DAYS),
                until=satisfaction_start,
            )
            waiting, handled, oldest = await reader.handoff_queue()
            _processing, stuck, _failed = await reader.document_health(
                stuck_before=now - STUCK_AFTER
            )
            knowledge = await reader.knowledge_summary()
            ingestion = await reader.ingestion_tokens_since(since=month_start_utc(now))

        return TenantActivity(
            generated_at=now,
            daily=daily,
            questions=compare(daily, "questions"),
            conversations=compare(daily, "conversations_started"),
            handoffs=compare(daily, "handoffs"),
            helpful=PeriodComparison(current=helpful, previous=prev_helpful),
            not_helpful=PeriodComparison(current=not_helpful, previous=prev_not_helpful),
            waiting_handoffs=waiting,
            handled_handoffs=handled,
            oldest_waiting_at=oldest,
            documents_stuck=stuck,
            knowledge=knowledge,
            # The reader is scoped to this tenant, so this is at most one entry.
            ingestion_tokens_this_month=ingestion.get(tenant_id, 0),
        )


__all__ = [
    "SATISFACTION_DAYS",
    "TREND_DAYS",
    "VIEW_ACTIVITY_PERMISSION",
    "GetTenantActivity",
    "TenantActivity",
    "TenantActivityQuery",
]
