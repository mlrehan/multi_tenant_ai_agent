"""The tenant dashboard's activity read.

Shares the platform's comparison helpers on purpose -- these pin that the
tenant path uses them (a week computed differently for the same tenant on two
dashboards is the failure) and that it is gated like conversations are.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from iam_platform.application.ai_resources.exceptions import PermissionDeniedError
from iam_platform.application.ai_resources.ports import KnowledgeSummary
from iam_platform.application.ai_resources.tenant_activity import (
    VIEW_ACTIVITY_PERMISSION,
    GetTenantActivity,
    TenantActivityQuery,
)
from iam_platform.core.clock import FixedClock
from tests.unit.ai_resources.test_platform_activity import NOW, TODAY, _day, _Reader

pytestmark = pytest.mark.asyncio


class _TenantReader(_Reader):
    async def knowledge_summary(self) -> KnowledgeSummary:
        return KnowledgeSummary(ready=25, processing=1, failed=2, web_pages=20, files=6,
                                last_added_at=NOW - timedelta(days=1))

    async def ingestion_tokens_since(self, *, since: datetime) -> dict[UUID, int]:
        # The month the allowance uses: from the 1st, 00:00 UTC.
        self.ingestion_since = since
        return {UUID("00000000-0000-0000-0000-000000000002"): 319_895}


class _Uow:
    def __init__(self, reader: _Reader) -> None:
        self.activity = reader

    async def __aenter__(self) -> _Uow:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


def _use_case(reader: _Reader) -> GetTenantActivity:
    return GetTenantActivity(lambda _a, _t: _Uow(reader), FixedClock(NOW))  # type: ignore[arg-type,return-value]


def _query(permissions: frozenset[str]) -> TenantActivityQuery:
    return TenantActivityQuery(
        actor_user_id="00000000-0000-0000-0000-000000000001",
        tenant_id="00000000-0000-0000-0000-000000000002",
        permissions=permissions,
    )


async def test_without_the_conversations_permission_nothing_is_read() -> None:
    reader = _TenantReader([])
    with pytest.raises(PermissionDeniedError):
        await _use_case(reader).execute(_query(frozenset({"tenant.resources.read"})))
    assert reader.since_seen is None


async def test_weeks_and_knowledge_are_reported_like_the_platform_does() -> None:
    rows = [_day(TODAY - timedelta(days=n), 3) for n in range(7)] + [
        _day(TODAY - timedelta(days=n), 1) for n in range(7, 14)
    ]
    reader = _TenantReader(rows)
    result = await _use_case(reader).execute(_query(frozenset({VIEW_ACTIVITY_PERMISSION})))

    assert (result.questions.current, result.questions.previous) == (21, 7)
    assert len(result.daily) == 14 and result.daily[-1].day == TODAY
    assert result.generated_at == NOW.astimezone(UTC)
    assert (result.knowledge.ready, result.knowledge.failed, result.knowledge.web_pages) == (25, 2, 20)
    assert (result.waiting_handoffs, result.handled_handoffs) == (2, 1)
    assert result.documents_stuck == 1
    assert (result.helpful.current, result.not_helpful.current) == (8, 2)
    # Ingestion is reported over the allowance's own month, from this tenant.
    assert result.ingestion_tokens_this_month == 319_895
    assert reader.ingestion_since == NOW.astimezone(UTC).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
