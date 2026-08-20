"""Per-tenant AI quotas: messages per day, tokens per calendar month.

Sits alongside `widget_quota.py` (per *widget* daily questions) rather than
replacing it. The two bound different things and a tenant hitting either should
stop: a widget's cap protects one embed from running away, a tenant's cap
protects the account. Merging them would mean a tenant with ten widgets could
spend ten times its allowance.

**Concurrency is the requirement, and `INCR` is the answer.** The naive
`SELECT count` then `UPDATE` loses under concurrency in the direction that
costs money -- two requests both read 999 against a limit of 1000 and both
proceed. Redis `INCR` is atomic and returns the post-increment value, so the
check and the reservation are the same operation and there is no window
between them. Every counter here is `INCR`-based for that reason.

**The two counters are shaped differently, deliberately, because the costs are
known at different times.**

* A *message* costs exactly one unit, known before any work happens. So the
  daily counter **consumes first** and the request proceeds only if the
  returned count is within the limit. This is a reservation.
* *Tokens* cost an unknown amount, known only once the provider has answered.
  So the monthly counter **reads before and records after**. One answer can
  therefore cross the line rather than being stopped exactly at it -- accepted
  deliberately, because the alternative is estimating up front and either
  over-refusing on a bad guess or being wrong anyway. The limit bounds a month,
  not a single response.

**Both reads fail closed.** An unconfirmable quota must not silently become an
unlimited one; that failure is invisible until the invoice, which is the entire
reason these counters exist. Recording failures fail *open* -- the tenant
already has their answer and the provider has already charged for it, so
raising then would show an error for work that succeeded without un-spending
the money.

**Windows are UTC.** This platform stores no tenant timezone (there is no
`tenant_settings` table), so inventing one here would mean picking a timezone
per tenant with nothing to read it from. UTC is stated rather than assumed, and
the key carries the date so the boundary is the key's, not an expiry race.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from redis.asyncio import Redis

logger = logging.getLogger("iam_platform.infrastructure.cache.tenant_quota")

#: Comfortably over a day / two months, so a key written at the very end of a
#: window survives long enough to be reported on. The date in the key defines
#: the window; expiry is only cleanup.
_DAY_TTL_SECONDS = 26 * 60 * 60
_MONTH_TTL_SECONDS = 62 * 24 * 60 * 60


class QuotaUnavailableError(Exception):
    """A quota could not be confirmed.

    Raised rather than returned so a caller cannot mistake "unknown" for
    "zero" -- the two differ by an entire allowance, and defaulting to zero is
    exactly the fail-open this module exists to avoid.
    """


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Provider usage, normalised.

    Every provider reports these three under its own names (OpenAI:
    `prompt_tokens`/`completion_tokens`; Anthropic: `input_tokens`/
    `output_tokens`). Normalising at the adapter boundary means the quota
    store, the API and the console all speak one vocabulary, and adding a
    provider does not ripple outward.

    `total` is carried rather than computed because providers can report a
    total larger than the sum -- cached and reasoning tokens are billed and do
    not always appear in either half. Recomputing it here would under-count the
    bill.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    @property
    def billable(self) -> int:
        return self.total_tokens or (self.input_tokens + self.output_tokens)


def _day_key(tenant_id: UUID) -> str:
    return f"tenant-messages:{tenant_id}:{datetime.now(UTC):%Y-%m-%d}"


def _month_key(tenant_id: UUID, suffix: str = "total") -> str:
    return f"tenant-tokens:{tenant_id}:{datetime.now(UTC):%Y-%m}:{suffix}"


class RedisTenantQuotaStore:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    # -- daily messages ------------------------------------------------------

    async def consume_message(self, *, tenant_id: UUID, limit: int | None) -> bool:
        """Reserves one AI message against today's allowance.

        `limit is None` means uncapped -- the counter is still incremented, so
        the console can report usage for a tenant who has no cap.

        **Only called on the AI path.** A human agent's reply, an internal
        comment, a system event and a request refused before any AI work all
        skip this entirely: they cost the platform nothing in inference, and
        charging them against an AI allowance would let a busy support team
        exhaust the chatbot's quota by answering tickets.
        """
        key = _day_key(tenant_id)
        try:
            pipe = self._redis.pipeline()
            pipe.incr(key)
            # `nx=True` so the TTL is set once, by the day's first message.
            # Without it every message pushes expiry forward and a busy
            # tenant's counter never resets.
            pipe.expire(key, _DAY_TTL_SECONDS, nx=True)
            count, _ = await pipe.execute()
        except Exception:
            logger.exception(
                "daily message quota could not be checked for tenant %s -- refusing",
                tenant_id,
            )
            return False
        return limit is None or int(count) <= limit

    async def release_message(self, *, tenant_id: UUID) -> None:
        """Gives back a reservation for work that never happened.

        Needed because `consume_message` reserves *before* the answer is
        attempted -- a request that then fails on a guardrail or an unreachable
        provider would otherwise permanently consume a message the tenant never
        received. Fails open: an unreturned reservation over-counts by one,
        which is the safe direction.
        """
        try:
            await self._redis.decr(_day_key(tenant_id))
        except Exception:
            logger.warning(
                "could not release a message reservation for tenant %s", tenant_id
            )

    async def messages_used_today(self, *, tenant_id: UUID) -> int:
        try:
            raw = await self._redis.get(_day_key(tenant_id))
        except Exception as exc:
            raise QuotaUnavailableError(str(exc)) from exc
        return int(raw) if raw is not None else 0

    # -- monthly tokens ------------------------------------------------------

    async def tokens_used_this_month(self, *, tenant_id: UUID) -> int:
        """Fails closed: see the module docstring."""
        try:
            raw = await self._redis.get(_month_key(tenant_id))
        except Exception as exc:
            logger.exception("token quota could not be read for tenant %s", tenant_id)
            raise QuotaUnavailableError(str(exc)) from exc
        return int(raw) if raw is not None else 0

    async def token_breakdown(self, *, tenant_id: UUID) -> TokenUsage:
        """Input/output/total for the console. Fails closed, like the read above."""
        try:
            values = await self._redis.mget(
                _month_key(tenant_id, "input"),
                _month_key(tenant_id, "output"),
                _month_key(tenant_id, "total"),
            )
        except Exception as exc:
            raise QuotaUnavailableError(str(exc)) from exc
        return TokenUsage(
            input_tokens=int(values[0] or 0),
            output_tokens=int(values[1] or 0),
            total_tokens=int(values[2] or 0),
        )

    async def record_tokens(self, *, tenant_id: UUID, usage: TokenUsage) -> None:
        """Adds one answer's usage. Fails open -- the money is already spent.

        All three counters move together in one pipeline so a reader cannot see
        a total that disagrees with its parts by more than one in-flight
        answer.
        """
        if usage.billable <= 0:
            return
        try:
            pipe = self._redis.pipeline()
            for suffix, amount in (
                ("input", usage.input_tokens),
                ("output", usage.output_tokens),
                ("total", usage.billable),
            ):
                if amount <= 0:
                    continue
                key = _month_key(tenant_id, suffix)
                pipe.incrby(key, amount)
                pipe.expire(key, _MONTH_TTL_SECONDS, nx=True)
            await pipe.execute()
        except Exception:
            logger.exception(
                "token usage of %s could not be recorded for tenant %s",
                usage.billable,
                tenant_id,
            )


class UnlimitedTenantQuotaStore:
    """For tests that are not about quota, and for nothing else.

    Deliberately not a production fallback for an unconfigured Redis: that
    would reintroduce fail-open through the back door, which is the one outcome
    `RedisTenantQuotaStore`'s reads refuse to allow.
    """

    def __init__(self) -> None:
        self.messages: list[UUID] = []
        self.released: list[UUID] = []
        self.tokens: list[tuple[UUID, TokenUsage]] = []

    async def consume_message(self, *, tenant_id: UUID, limit: int | None) -> bool:
        del limit
        self.messages.append(tenant_id)
        return True

    async def release_message(self, *, tenant_id: UUID) -> None:
        self.released.append(tenant_id)

    async def messages_used_today(self, *, tenant_id: UUID) -> int:
        return len([t for t in self.messages if t == tenant_id])

    async def tokens_used_this_month(self, *, tenant_id: UUID) -> int:
        return sum(u.billable for t, u in self.tokens if t == tenant_id)

    async def token_breakdown(self, *, tenant_id: UUID) -> TokenUsage:
        mine = [u for t, u in self.tokens if t == tenant_id]
        return TokenUsage(
            input_tokens=sum(u.input_tokens for u in mine),
            output_tokens=sum(u.output_tokens for u in mine),
            total_tokens=sum(u.billable for u in mine),
        )

    async def record_tokens(self, *, tenant_id: UUID, usage: TokenUsage) -> None:
        self.tokens.append((tenant_id, usage))
