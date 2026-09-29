"""The operator dashboard's activity read.

What these pin is the arithmetic the console shows as claims -- "up 40% on
last week", "83% helpful" -- because a comparison the server gets wrong is
repeated faithfully by every client. The SQL itself is exercised live; these
drive the real use case with a fake reader.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest

from iam_platform.application.ai_resources import platform_activity
from iam_platform.application.ai_resources.exceptions import (
    ModelConfigurationManagementDeniedError,
)
from iam_platform.application.ai_resources.ports import DailyActivityCounts
from iam_platform.core.clock import FixedClock

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 9, 27, 15, 30, tzinfo=UTC)
TODAY = NOW.date()


def _day(d: date, questions: int = 0, **kw: int) -> DailyActivityCounts:
    return DailyActivityCounts(
        day=d,
        conversations_started=kw.get("conversations", 0),
        questions=questions,
        answers=kw.get("answers", questions),
        handoffs=kw.get("handoffs", 0),
        tokens=kw.get("tokens", 0),
    )


class _Reader:
    def __init__(self, rows: list[DailyActivityCounts]) -> None:
        self.rows = rows
        self.feedback_windows: list[tuple[datetime, datetime]] = []
        self.since_seen: datetime | None = None
        self.stuck_before: datetime | None = None

    async def daily_counts(self, *, since: datetime) -> list[DailyActivityCounts]:
        self.since_seen = since
        return self.rows

    async def feedback_counts(self, *, since: datetime, until: datetime) -> tuple[int, int]:
        self.feedback_windows.append((since, until))
        # Current window 8 up / 2 down; previous window 3 up / 3 down.
        return (8, 2) if until == NOW else (3, 3)

    async def active_tenants(self, *, since: datetime) -> int:
        return 1

    async def handoff_queue(self) -> tuple[int, int, datetime | None]:
        return 2, 1, NOW - timedelta(minutes=12)

    async def document_health(self, *, stuck_before: datetime) -> tuple[int, int, int]:
        self.stuck_before = stuck_before
        return 3, 1, 4

    async def attention(self, *, stuck_before: datetime) -> list[object]:
        return []

    async def usage_recorded_since(self) -> datetime | None:
        return None


class _Uow:
    def __init__(self, reader: _Reader) -> None:
        self.activity = reader

    async def __aenter__(self) -> _Uow:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


async def _run(
    monkeypatch: pytest.MonkeyPatch,
    rows: list[DailyActivityCounts],
    permissions: frozenset[str] = frozenset({"platform.model_configurations.manage"}),
):  # type: ignore[no-untyped-def]
    async def fake_state(uow: object, actor: object, *, now: object) -> object:
        return SimpleNamespace(permissions=permissions)

    monkeypatch.setattr(platform_activity, "compute_effective_platform_state", fake_state)
    reader = _Reader(rows)
    use_case = platform_activity.GetPlatformActivity(
        lambda _actor: _Uow(reader), FixedClock(NOW)  # type: ignore[arg-type,return-value]
    )
    result = await use_case.execute(
        platform_activity.PlatformActivityQuery(actor_user_id="00000000-0000-0000-0000-000000000001")
    )
    return result, reader


class TestTheSeries:
    async def test_it_is_fourteen_days_ending_today_with_quiet_days_as_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result, reader = await _run(monkeypatch, [_day(TODAY, 5), _day(TODAY - timedelta(days=3), 2)])

        assert len(result.daily) == 14
        assert result.daily[-1].day == TODAY
        assert result.daily[0].day == TODAY - timedelta(days=13)
        assert [d.questions for d in result.daily].count(0) == 12
        # The window starts at the first day's UTC midnight, not "now minus 14 days".
        assert reader.since_seen == datetime(2026, 9, 14, tzinfo=UTC)


class TestWeekOnWeek:
    async def test_the_last_seven_days_are_compared_with_the_seven_before(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rows = [_day(TODAY - timedelta(days=n), 10) for n in range(7)] + [
            _day(TODAY - timedelta(days=n), 4) for n in range(7, 14)
        ]
        result, _ = await _run(monkeypatch, rows)

        assert (result.questions.current, result.questions.previous) == (70, 28)

    async def test_the_boundary_day_belongs_to_the_current_week(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Day 6 back is the oldest of the current seven; day 7 back is the
        newest of the previous seven. An off-by-one here moves a day's traffic
        between the two halves of every comparison."""
        result, _ = await _run(
            monkeypatch,
            [_day(TODAY - timedelta(days=6), 1), _day(TODAY - timedelta(days=7), 100)],
        )
        assert (result.questions.current, result.questions.previous) == (1, 100)


class TestSatisfaction:
    async def test_the_current_and_previous_thirty_days_are_separate_windows(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result, reader = await _run(monkeypatch, [])

        assert (result.helpful.current, result.not_helpful.current) == (8, 2)
        assert (result.helpful.previous, result.not_helpful.previous) == (3, 3)
        (cur_since, cur_until), (prev_since, prev_until) = reader.feedback_windows
        assert cur_until == NOW and cur_since == NOW - timedelta(days=30)
        # Adjacent, not overlapping: no rating is counted in both.
        assert prev_until == cur_since and prev_since == cur_since - timedelta(days=30)


class TestOpenProblems:
    async def test_stuck_means_processing_for_longer_than_thirty_minutes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result, reader = await _run(monkeypatch, [])
        assert reader.stuck_before == NOW - timedelta(minutes=30)
        assert (result.documents_processing, result.documents_stuck, result.documents_failed) == (3, 1, 4)
        assert (result.waiting_handoffs, result.handled_handoffs) == (2, 1)


class TestPermission:
    async def test_without_the_operator_permission_nothing_is_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with pytest.raises(ModelConfigurationManagementDeniedError):
            await _run(monkeypatch, [], permissions=frozenset())
