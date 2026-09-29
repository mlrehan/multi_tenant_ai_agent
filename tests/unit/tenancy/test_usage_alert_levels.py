"""The 80/90/95/100% usage thresholds, shared by the tenant and the operator."""

from __future__ import annotations

import pytest

from iam_platform.domain.tenancy.entitlements import usage_alert_level

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("used", "limit", "level"),
    [
        (0, 100, None),
        (79, 100, None),
        (80, 100, 80),
        (89, 100, 80),
        (90, 100, 90),
        (94, 100, 90),
        (95, 100, 95),
        (99, 100, 95),
        (100, 100, 100),
        (140, 100, 100),  # one answer may overshoot; still exhausted
        (799_999, 1_000_000, None),
        (800_000, 1_000_000, 80),  # exact, with no floating-point rounding
        (949_999, 1_000_000, 90),
        (39_445, 50, 100),
        (40, 50, 80),
        (0, 0, 100),  # a limit of 0 means "none at all": exhausted, not an error
    ],
)
def test_levels(used: int, limit: int, level: int | None) -> None:
    assert usage_alert_level(used=used, limit=limit) == level


@pytest.mark.parametrize(("used", "limit"), [(None, 100), (50, None), (None, None)])
def test_unknown_or_uncapped_is_never_an_alert(used: int | None, limit: int | None) -> None:
    assert usage_alert_level(used=used, limit=limit) is None
