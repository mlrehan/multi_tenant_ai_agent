"""Daily question quota for public chat widgets, in Redis.

**Fails closed, and that is the whole design decision.** Everywhere else in
this platform Redis failing closed means denying access someone was entitled to
— an inconvenience. Here it means declining to spend money on behalf of a
tenant whose limit cannot be confirmed. A quota store that failed *open* would
turn "Redis is down" into "every widget is unlimited", which is the one outcome
this counter exists to prevent, and it would be invisible until the bill
arrived.

The counter is a calendar-day fixed window rather than a rolling one. A rolling
window needs per-request timestamps and a sorted set; a daily cap is a spending
control, not a burst control, and the burst case is already covered by the
per-IP rate-limit middleware. Simplicity wins where the extra precision buys
nothing.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from redis.asyncio import Redis

logger = logging.getLogger("iam_platform.infrastructure.cache.widget_quota")

#: Slightly over 24 hours, so a key set at 23:59 survives until the next day's
#: window has clearly begun. Expiry is a cleanup mechanism here, not the window
#: boundary -- the date in the key is what defines the day.
_KEY_TTL_SECONDS = 26 * 60 * 60


class RedisWidgetQuotaStore:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def consume(self, *, widget_id: UUID, limit: int) -> bool:
        """Records one question; returns whether it was within today's limit."""
        key = f"widget-quota:{widget_id}:{datetime.now(UTC):%Y-%m-%d}"
        try:
            pipe = self._redis.pipeline()
            pipe.incr(key)
            # `nx=True` so the TTL is set once, on the first question of the
            # day. Without it every question would push the expiry forward and
            # a busy widget's counter would never reset.
            pipe.expire(key, _KEY_TTL_SECONDS, nx=True)
            count, _ = await pipe.execute()
        except Exception:
            # Deny. See the module docstring: an unconfirmable limit must not
            # become an unlimited one.
            logger.exception(
                "widget quota could not be checked for %s -- refusing the question",
                widget_id,
            )
            return False
        return int(count) <= limit


class UnlimitedWidgetQuotaStore:
    """For tests that are not about quota.

    Deliberately *not* used as a production fallback when Redis is
    unconfigured: that would reintroduce fail-open through the back door. The
    composition root wires the Redis store unconditionally.
    """

    def __init__(self) -> None:
        self.consumed: list[UUID] = []

    async def consume(self, *, widget_id: UUID, limit: int) -> bool:
        del limit
        self.consumed.append(widget_id)
        return True
