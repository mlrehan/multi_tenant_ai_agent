"""The platform dashboard must show the daily limit that is actually enforced.

The tenant row once showed "0 / 100" -- the platform ceiling -- for a tenant
whose own setting capped it at 50, so the operator was told the wrong
allowance and the platform and tenant dashboards disagreed about one number.
"""

from uuid import uuid4

from iam_platform.application.ai_resources.platform_overview import TenantSpend


def _row(*, ceiling: int | None, enforced: int | None, used: int | None) -> TenantSpend:
    return TenantSpend(
        tenant_id=uuid4(),
        slug="acme",
        display_name="Acme",
        max_tokens_per_month=None,
        used_tokens=0,
        max_messages_per_day=ceiling,
        used_messages_today=used,
        effective_messages_per_day=enforced,
    )


def test_remaining_is_measured_against_the_enforced_limit_not_the_ceiling() -> None:
    row = _row(ceiling=100, enforced=50, used=10)
    assert row.remaining_messages_today == 40


def test_remaining_never_goes_negative() -> None:
    assert _row(ceiling=100, enforced=50, used=60).remaining_messages_today == 0


def test_an_uncapped_tenant_has_no_remaining_figure() -> None:
    assert _row(ceiling=None, enforced=None, used=10).remaining_messages_today is None


def test_an_unreadable_counter_is_unknown_not_zero() -> None:
    assert _row(ceiling=100, enforced=50, used=None).remaining_messages_today is None


def test_the_message_alert_is_measured_against_the_enforced_limit() -> None:
    # 45 of an enforced 50 is 90% -- against the ceiling of 100 it would be 45%
    # and the operator would not be warned while the tenant is.
    assert _row(ceiling=100, enforced=50, used=45).message_alert_level == 90
