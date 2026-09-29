"""The durable AI usage ledger, in Postgres.

**Its own short session per call**, not a repository on a unit of work. Two
callers need it and neither holds a unit of work at the time: the answer path
records after the stream has finished (the request's transaction is long
closed by then), and the Redis counters read it on a cache miss from inside
infrastructure, where there is no unit of work to borrow.

**Runs on the RLS-subject `app_tenant` engine with the tenant set**, exactly as
a tenant unit of work does -- so every query here is confined by the database
to the tenant it names, and the explicit `tenant_id` filters are a second,
redundant fence rather than the only one. The BYPASSRLS platform engine is
deliberately not used: a per-tenant sum has no reason to be able to see other
tenants' rows.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import Table, func, insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from iam_platform.application.ai_resources.ports import (
    ANSWER_CHANNELS,
    TokenUsage,
    UsageEvent,
)
from iam_platform.infrastructure.db.models.ai_resources import AiUsageEventModel


class SqlUsageLedger:
    def __init__(self, session_factory: Callable[[], AsyncSession]) -> None:
        self._session_factory = session_factory

    async def _scoped(self, session: AsyncSession, tenant_id: UUID) -> None:
        # Transaction-scoped (`true`), so a pooled connection cannot carry this
        # tenant into its next use -- the same rule every unit of work follows.
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": str(tenant_id)}
        )

    async def record(self, event: UsageEvent) -> None:
        async with self._session_factory() as session, session.begin():
            await self._scoped(session, event.tenant_id)
            # A Core INSERT, not `session.add`. An ORM flush sorts tables by
            # their foreign keys and so has to resolve `tenants` -- which the
            # worker process never imports. There every ingestion row failed
            # with `NoReferencedTableError`, caught and logged by the fail-open
            # caller, so documents indexed and no spend was ever recorded. The
            # API imports every model, which is why answers were unaffected.
            await session.execute(
                insert(cast(Table, AiUsageEventModel.__table__)).values(
                    tenant_id=event.tenant_id,
                    occurred_at=event.occurred_at,
                    channel=event.channel,
                    model_configuration_id=event.model_configuration_id,
                    input_tokens=max(0, event.input_tokens),
                    output_tokens=max(0, event.output_tokens),
                    total_tokens=max(0, event.total_tokens),
                    embedding_tokens=min(max(0, event.embedding_tokens), max(0, event.input_tokens)),
                    chat_model=event.chat_model,
                    embedding_model=event.embedding_model,
                )
            )

    async def tokens_since(self, *, tenant_id: UUID, since: datetime) -> TokenUsage:
        async with self._session_factory() as session, session.begin():
            await self._scoped(session, tenant_id)
            row = (
                await session.execute(
                    select(
                        func.coalesce(func.sum(AiUsageEventModel.input_tokens), 0),
                        func.coalesce(func.sum(AiUsageEventModel.output_tokens), 0),
                        func.coalesce(func.sum(AiUsageEventModel.total_tokens), 0),
                    ).where(
                        AiUsageEventModel.tenant_id == tenant_id,
                        AiUsageEventModel.occurred_at >= since,
                        # Answers only. This seeds the counter the chat
                        # allowance is enforced against; indexing a document
                        # is metered in the same table but is not part of it.
                        AiUsageEventModel.channel.in_(ANSWER_CHANNELS),
                    )
                )
            ).one()
        return TokenUsage(
            input_tokens=int(row[0]), output_tokens=int(row[1]), total=int(row[2])
        )

    async def configuration_tokens_since(
        self, *, tenant_id: UUID, model_configuration_id: UUID, since: datetime
    ) -> int:
        async with self._session_factory() as session, session.begin():
            await self._scoped(session, tenant_id)
            total = await session.scalar(
                select(func.coalesce(func.sum(AiUsageEventModel.total_tokens), 0)).where(
                    AiUsageEventModel.tenant_id == tenant_id,
                    AiUsageEventModel.model_configuration_id == model_configuration_id,
                    AiUsageEventModel.occurred_at >= since,
                )
            )
        return int(total or 0)

    async def answers_since(self, *, tenant_id: UUID, since: datetime) -> int:
        async with self._session_factory() as session, session.begin():
            await self._scoped(session, tenant_id)
            count = await session.scalar(
                select(func.count())
                .select_from(AiUsageEventModel)
                .where(
                    AiUsageEventModel.tenant_id == tenant_id,
                    AiUsageEventModel.occurred_at >= since,
                    # One ingestion row per indexed document; counting those
                    # would rebuild the day's *message* count wrongly high.
                    AiUsageEventModel.channel.in_(ANSWER_CHANNELS),
                )
            )
        return int(count or 0)
