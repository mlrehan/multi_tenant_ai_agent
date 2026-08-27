"""The daily allowance resets on the tenant's midnight, not UTC's.

A nursery in British Summer Time had its counter roll over at 01:00 local, and
a message sent at 00:30 counted against the previous day. Consistent, never
wrong by more than an hour, and not what anyone means by "messages today".

**The hazard this introduces is worse than the bug it fixes**, which is why
these tests exist: the Redis key *is* the window, so if the write path and the
read path resolve the zone differently, one number is enforced and a different
one displayed -- and nothing looks broken until a tenant counts their own
messages by hand.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from iam_platform.domain.ai_resources.chatbot import TenantChatbotSettings
from iam_platform.infrastructure.cache.tenant_quota import _day_key

TENANT = uuid4()
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _settings(zone: str) -> TenantChatbotSettings:
    return TenantChatbotSettings(
        id=uuid4(),
        tenant_id=TENANT,
        quota_timezone=zone,
        created_at=NOW,
        updated_at=NOW,
    )


class TestTheZoneIsResolvedSafely:
    def test_a_valid_iana_name_is_used(self) -> None:
        assert _settings("Europe/London").quota_day_zone() == ZoneInfo("Europe/London")

    def test_an_unknown_zone_degrades_to_utc_rather_than_raising(self) -> None:
        """This runs on the answer path. A mistyped timezone must not take a
        tenant's chatbot down -- it earns them the off-by-an-hour boundary they
        had before this existed, which is strictly better than no service."""
        assert _settings("Mars/Olympus_Mons").quota_day_zone() is UTC

    def test_an_empty_zone_degrades_to_utc(self) -> None:
        assert _settings("").quota_day_zone() is UTC

    def test_the_default_is_utc(self) -> None:
        """NOT NULL with a default, so no reader has to invent a fallback --
        two readers inventing different ones is the whole hazard."""
        settings = TenantChatbotSettings(
            id=uuid4(), tenant_id=TENANT, created_at=NOW, updated_at=NOW
        )
        assert settings.quota_timezone == "UTC"


class TestTheKeyIsTheWindow:
    def test_the_same_zone_always_yields_the_same_key(self) -> None:
        """Read and write must land on one key or they describe different
        days."""
        zone = ZoneInfo("Europe/London")
        assert _day_key(TENANT, zone) == _day_key(TENANT, zone)

    def test_no_zone_matches_utc(self) -> None:
        """Counters written before tenant timezones existed used the UTC date,
        and an omitted zone must keep reading them rather than silently
        starting a new window."""
        assert _day_key(TENANT) == _day_key(TENANT, UTC)

    def test_zones_a_day_apart_never_share_a_counter(self) -> None:
        """The actual fix, stated so it holds at every instant.

        Kiritimati is UTC+14 and Niue is UTC-11 -- 25 hours apart, so their
        local calendar dates can *never* be the same, whatever the wall clock
        says. An earlier version of this test compared a zone against UTC and
        hedged on whether the dates happened to differ right now; it therefore
        passed with the timezone support deleted, which is worse than no test.
        """
        kiritimati = ZoneInfo("Pacific/Kiritimati")
        niue = ZoneInfo("Pacific/Niue")
        assert _day_key(TENANT, kiritimati) != _day_key(TENANT, niue)

    def test_different_tenants_never_share_a_key(self) -> None:
        other = uuid4()
        assert _day_key(TENANT, UTC) != _day_key(other, UTC)
