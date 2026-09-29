"""Monthly token spend per model configuration, in Redis.

Sits beside `widget_quota.py` and shares its fail-closed reasoning for the
*check*, but deliberately differs in shape, and the difference is the whole
design:

**The widget quota consumes before the work; this one records after it.** A
question costs one unit of widget quota, known before anything happens. A
question costs an unknown number of *tokens*, known only once the model has
finished answering. So there is nothing to consume up front here — the budget
is checked against what earlier answers already spent, and the answer in flight
is added when it completes. One answer can therefore push a tenant over its
limit by its own size; the next one is refused. That is the intended
granularity: `token_budget_per_month` bounds a month of spending, not a single
response.

**Reading fails closed, recording fails open**, and they are asymmetric on
purpose. A budget that cannot be read must not silently become unlimited — that
is the failure this counter exists to prevent, and it is invisible until the
bill. But a *recording* failure happens after the tenant already has their
answer and OpenAI has already charged for it; raising then would show an error
for work that succeeded, and would not un-spend the money. Losing a count is
the smaller harm, and it is logged.

The window is a calendar month keyed by `YYYY-MM`, matching how the field reads
to an operator ("per month") and how billing periods are usually discussed. A
rolling 30-day window would need per-request timestamps and buy nothing here.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from redis.asyncio import Redis

from iam_platform.application.ai_resources.ports import UsageLedger

logger = logging.getLogger("iam_platform.infrastructure.cache.token_usage")

#: Two months, so a key written on the last day of a month is still readable
#: while that month is being reported on, and disappears on its own afterwards.
#: The `YYYY-MM` in the key is what defines the window; expiry is only cleanup.
_KEY_TTL_SECONDS = 62 * 24 * 60 * 60


def _key(tenant_id: UUID, model_configuration_id: UUID) -> str:
    return _key_at(tenant_id, model_configuration_id, datetime.now(UTC))


def _key_at(tenant_id: UUID, model_configuration_id: UUID, now: datetime) -> str:
    return f"token-usage:{tenant_id}:{model_configuration_id}:{now:%Y-%m}"


class BudgetUnavailableError(Exception):
    """The month's spend could not be read.

    Raised rather than returned so a caller cannot mistake "unknown" for
    "zero" — the two differ by an entire month's budget, and defaulting to zero
    is exactly the fail-open this module exists to avoid.
    """


class RedisTokenUsageStore:
    def __init__(self, redis: Redis, *, ledger: UsageLedger | None = None) -> None:
        self._redis = redis
        # Seeds a missing key from Postgres -- the same rule, and the same
        # reasons, as `tenant_quota.RedisTenantQuotaStore`.
        self._ledger = ledger

    async def _seeded_key(self, tenant_id: UUID, model_configuration_id: UUID) -> str:
        now = datetime.now(UTC)
        key = _key_at(tenant_id, model_configuration_id, now)
        if self._ledger is not None and not await self._redis.exists(key):
            spent = await self._ledger.configuration_tokens_since(
                tenant_id=tenant_id,
                model_configuration_id=model_configuration_id,
                since=datetime(now.year, now.month, 1, tzinfo=UTC),
            )
            await self._redis.set(key, spent, nx=True, ex=_KEY_TTL_SECONDS)
        return key

    async def read(self, *, tenant_id: UUID, model_configuration_id: UUID) -> int:
        try:
            raw = await self._redis.get(
                await self._seeded_key(tenant_id, model_configuration_id)
            )
        except Exception as exc:
            logger.exception(
                "token budget could not be read for tenant %s / configuration %s",
                tenant_id,
                model_configuration_id,
            )
            raise BudgetUnavailableError(str(exc)) from exc
        return int(raw) if raw is not None else 0

    async def record(
        self, *, tenant_id: UUID, model_configuration_id: UUID, tokens: int
    ) -> None:
        if tokens <= 0:
            return
        try:
            # Seeded first; a seeding failure skips the increment rather than
            # creating a key that hides the month's history.
            key = await self._seeded_key(tenant_id, model_configuration_id)
            pipe = self._redis.pipeline()
            pipe.incrby(key, tokens)
            # `nx=True` so the TTL is set once, by the first answer of the
            # month. Without it every answer would push expiry forward and a
            # busy tenant's counter would never age out.
            pipe.expire(key, _KEY_TTL_SECONDS, nx=True)
            await pipe.execute()
        except Exception:
            # Fails open by design -- see the module docstring. The answer is
            # already delivered and already paid for; the next check reads a
            # slightly low number rather than erroring at someone who did
            # nothing wrong.
            logger.exception(
                "token usage of %s could not be recorded for tenant %s / configuration %s",
                tokens,
                tenant_id,
                model_configuration_id,
            )


class UnlimitedTokenUsageStore:
    """For tests that are not about budgets, and for nothing else.

    Deliberately not a production fallback when Redis is unconfigured: that
    would reintroduce fail-open through the back door, which is the one
    outcome `RedisTokenUsageStore.read` refuses to allow. The composition root
    wires the Redis store unconditionally.
    """

    def __init__(self) -> None:
        self.recorded: list[tuple[UUID, UUID, int]] = []

    async def read(self, *, tenant_id: UUID, model_configuration_id: UUID) -> int:
        del tenant_id, model_configuration_id
        return 0

    async def record(
        self, *, tenant_id: UUID, model_configuration_id: UUID, tokens: int
    ) -> None:
        self.recorded.append((tenant_id, model_configuration_id, tokens))
