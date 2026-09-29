"""Cross-tenant activity aggregates for the platform dashboard.

Plain SQL rather than ORM queries: each method is one GROUP BY or one
filtered COUNT, and the SQL is the clearest statement of what is counted.

**Days are UTC.** A platform-wide chart has no single tenant whose midnight it
could use, and mixing zones would put one tenant's evening into another
tenant's next day. Per-tenant *quota* days are the tenant's own (see
`tenant_quota.py`); this is a different question -- "what did the platform do"
-- and it is labelled UTC in the console.

**What counts as what**, stated once so the console can say it plainly:

- *question* -- a `user` turn, i.e. something a person asked the AI;
- *answer*   -- an `assistant` turn, i.e. something the AI replied;
- *handoff*  -- a conversation whose `handoff_at` falls on that day;
- *tokens*   -- the usage ledger, which begins on the day it was deployed.

Agent replies, internal notes and system events are deliberately not
"questions" or "answers": they cost no inference and are not AI activity.

**One reader, two scopes.** The platform constructs it unscoped on the
BYPASSRLS session and sees every tenant. A tenant's unit of work constructs it
with its own `tenant_id` on the RLS-subject session, and every query then also
carries `tenant_id = :tenant_id` -- a second fence behind RLS rather than the
only one, the same rule every tenant query in this codebase follows. The
scope is fixed at construction, not passed per call, so no caller can forget
it on one method.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from iam_platform.application.ai_resources.ports import (
    ANSWER_CHANNELS,
    INGESTION_CHANNEL,
    DailyActivityCounts,
    KnowledgeSummary,
    TenantAttentionCounts,
    UsageCostLine,
)

#: `('console','widget')`, built from the constant rather than retyped, so the
#: activity chart's token series and the Redis seed agree on what an answer is.
#: The values are fixed identifiers from code, never input, so inlining is safe.
_ANSWER_CHANNELS_SQL = "(" + ",".join(f"'{c}'" for c in ANSWER_CHANNELS) + ")"

#: Conversations waiting for a person, and ones a person is handling. Named
#: here, next to the SQL that uses them, so the two lists cannot drift from
#: each other -- `ConversationState` is the source of the values.
_WAITING = ("handoff_requested", "unassigned")
_BEING_HANDLED = ("assigned", "human_active")


class SqlPlatformActivityReader:
    def __init__(self, session: AsyncSession, *, tenant_id: UUID | None = None) -> None:
        self._session = session
        self._tenant_id = tenant_id

    def _and(self, column: str = "tenant_id") -> str:
        """` AND <column> = :tenant_id` when scoped to a tenant, else nothing."""
        return f" AND {column} = :tenant_id" if self._tenant_id is not None else ""

    def _params(self, **params: object) -> dict[str, object]:
        if self._tenant_id is not None:
            params["tenant_id"] = self._tenant_id
        return params

    async def daily_counts(self, *, since: datetime) -> list[DailyActivityCounts]:
        rows = (
            await self._session.execute(
                text(
                    """
                    WITH m AS (
                        SELECT (created_at AT TIME ZONE 'UTC')::date AS day,
                               count(*) FILTER (WHERE role = 'user') AS questions,
                               count(*) FILTER (WHERE role = 'assistant') AS answers
                        FROM conversation_messages
                        WHERE created_at >= :since{t}
                        GROUP BY 1
                    ), c AS (
                        SELECT (created_at AT TIME ZONE 'UTC')::date AS day, count(*) AS started
                        FROM conversations WHERE created_at >= :since{t} GROUP BY 1
                    ), h AS (
                        SELECT (handoff_at AT TIME ZONE 'UTC')::date AS day, count(*) AS handoffs
                        FROM conversations WHERE handoff_at >= :since{t} GROUP BY 1
                    ), u AS (
                        SELECT (occurred_at AT TIME ZONE 'UTC')::date AS day,
                               sum(total_tokens) AS tokens
                        FROM ai_usage_events
                        WHERE occurred_at >= :since AND channel IN {answer_channels}{t}
                        GROUP BY 1
                    ), days AS (
                        SELECT day FROM m UNION SELECT day FROM c
                        UNION SELECT day FROM h UNION SELECT day FROM u
                    )
                    SELECT days.day,
                           coalesce(c.started, 0), coalesce(m.questions, 0),
                           coalesce(m.answers, 0), coalesce(h.handoffs, 0),
                           coalesce(u.tokens, 0)
                    FROM days
                    LEFT JOIN m USING (day) LEFT JOIN c USING (day)
                    LEFT JOIN h USING (day) LEFT JOIN u USING (day)
                    ORDER BY days.day
                    """.format(t=self._and(), answer_channels=_ANSWER_CHANNELS_SQL)
                ),
                self._params(since=since),
            )
        ).all()
        return [
            DailyActivityCounts(
                day=r[0],
                conversations_started=int(r[1]),
                questions=int(r[2]),
                answers=int(r[3]),
                handoffs=int(r[4]),
                tokens=int(r[5]),
            )
            for r in rows
        ]

    async def feedback_counts(self, *, since: datetime, until: datetime) -> tuple[int, int]:
        row = (
            await self._session.execute(
                text(
                    f"""
                    SELECT count(*) FILTER (WHERE rating = 'up'),
                           count(*) FILTER (WHERE rating = 'down')
                    FROM answer_feedback
                    WHERE created_at >= :since AND created_at < :until{self._and()}
                    """
                ),
                self._params(since=since, until=until),
            )
        ).one()
        return int(row[0]), int(row[1])

    async def active_tenants(self, *, since: datetime) -> int:
        value = await self._session.scalar(
            text(
                f"""
                SELECT count(DISTINCT tenant_id) FROM conversation_messages
                WHERE role = 'user' AND created_at >= :since{self._and()}
                """
            ),
            self._params(since=since),
        )
        return int(value or 0)

    async def handoff_queue(self) -> tuple[int, int, datetime | None]:
        row = (
            await self._session.execute(
                text(
                    f"""
                    SELECT count(*) FILTER (WHERE state = ANY(:waiting)),
                           count(*) FILTER (WHERE state = ANY(:handled)),
                           min(handoff_at) FILTER (WHERE state = ANY(:waiting))
                    FROM conversations
                    WHERE status = 'active'{self._and()}
                    """
                ),
                self._params(waiting=list(_WAITING), handled=list(_BEING_HANDLED)),
            )
        ).one()
        return int(row[0]), int(row[1]), row[2]

    async def document_health(self, *, stuck_before: datetime) -> tuple[int, int, int]:
        row = (
            await self._session.execute(
                text(
                    f"""
                    SELECT count(*) FILTER (WHERE status = 'processing'),
                           count(*) FILTER (WHERE status = 'processing'
                                            AND created_at < :stuck_before),
                           count(*) FILTER (WHERE status = 'failed')
                    FROM documents
                    WHERE deleted_at IS NULL{self._and()}
                    """
                ),
                self._params(stuck_before=stuck_before),
            )
        ).one()
        return int(row[0]), int(row[1]), int(row[2])

    async def attention(self, *, stuck_before: datetime) -> list[TenantAttentionCounts]:
        rows = (
            await self._session.execute(
                text(
                    """
                    WITH w AS (
                        SELECT tenant_id, count(*) AS waiting, min(handoff_at) AS oldest
                        FROM conversations
                        WHERE status = 'active' AND state = ANY(:waiting)
                        GROUP BY tenant_id
                    ), d AS (
                        SELECT tenant_id,
                               count(*) FILTER (WHERE status = 'processing'
                                                AND created_at < :stuck_before) AS stuck,
                               count(*) FILTER (WHERE status = 'failed') AS failed
                        FROM documents WHERE deleted_at IS NULL
                        GROUP BY tenant_id
                    )
                    SELECT t.id, t.display_name, t.slug,
                           coalesce(w.waiting, 0), w.oldest,
                           coalesce(d.stuck, 0), coalesce(d.failed, 0)
                    FROM tenants t
                    LEFT JOIN w ON w.tenant_id = t.id
                    LEFT JOIN d ON d.tenant_id = t.id
                    WHERE coalesce(w.waiting, 0) + coalesce(d.stuck, 0)
                          + coalesce(d.failed, 0) > 0{t}
                    ORDER BY coalesce(w.waiting, 0) DESC, coalesce(d.stuck, 0) DESC,
                             coalesce(d.failed, 0) DESC, t.display_name
                    """.format(t=self._and("t.id"))
                ),
                self._params(waiting=list(_WAITING), stuck_before=stuck_before),
            )
        ).all()
        return [
            TenantAttentionCounts(
                tenant_id=UUID(str(r[0])),
                display_name=r[1],
                slug=r[2],
                waiting_handoffs=int(r[3]),
                oldest_waiting_at=r[4],
                stuck_documents=int(r[5]),
                failed_documents=int(r[6]),
            )
            for r in rows
        ]

    async def usage_recorded_since(self) -> datetime | None:
        value: datetime | None = await self._session.scalar(
            text(f"SELECT min(occurred_at) FROM ai_usage_events WHERE true{self._and()}"),
            self._params(),
        )
        return value

    async def ingestion_tokens_since(self, *, since: datetime) -> dict[UUID, int]:
        rows = (
            await self._session.execute(
                text(
                    "SELECT tenant_id, sum(total_tokens) FROM ai_usage_events "
                    f"WHERE channel = :channel AND occurred_at >= :since{self._and()} "
                    "GROUP BY tenant_id"
                ),
                self._params(channel=INGESTION_CHANNEL, since=since),
            )
        ).all()
        return {UUID(str(r[0])): int(r[1] or 0) for r in rows}

    async def usage_cost_lines(self, *, since: datetime) -> list[UsageCostLine]:
        """Each row priced at its own model's price in force when it occurred.

        One answer pays two models, so each row contributes up to two lines:
        its chat tokens (input minus embedding, plus output) at the chat
        model's price, and its embedding tokens at the embedding model's
        input price. An ingestion row is embedding only.

        A line with no price -- no recorded model, or no entry in force yet --
        is counted in `unpriced` and contributes nothing to the cost. For
        chat, the tokens counted are the larger of `total - embedding` and
        `input - embedding + output`, so a row whose provider total exceeds
        its split (older rows; reasoning or cached tokens) is not undercounted.
        """
        rows = (
            await self._session.execute(
                text(
                    f"""
                    WITH u AS (
                        SELECT tenant_id, occurred_at, chat_model, embedding_model,
                               CASE WHEN channel = :ingestion THEN input_tokens
                                    ELSE coalesce(embedding_tokens, 0) END AS emb,
                               CASE WHEN channel = :ingestion THEN 0
                                    ELSE greatest(input_tokens - coalesce(embedding_tokens, 0), 0)
                               END AS chat_in,
                               CASE WHEN channel = :ingestion THEN 0 ELSE output_tokens END
                                   AS chat_out,
                               CASE WHEN channel = :ingestion THEN 0
                                    ELSE greatest(total_tokens - coalesce(embedding_tokens, 0),
                                                  input_tokens - coalesce(embedding_tokens, 0)
                                                  + output_tokens, 0)
                               END AS chat_all
                        FROM ai_usage_events
                        WHERE occurred_at >= :since{self._and()}
                    ), lines AS (
                        SELECT u.tenant_id, 'chat' AS kind, u.chat_model AS model,
                               u.chat_all AS tokens, p.id IS NOT NULL AS priced,
                               (u.chat_in * p.input_usd_per_million
                                + u.chat_out * p.output_usd_per_million) / 1000000 AS cost
                        FROM u
                        LEFT JOIN LATERAL (
                            SELECT id, input_usd_per_million, output_usd_per_million
                            FROM ai_model_prices
                            WHERE model_name = u.chat_model AND effective_from <= u.occurred_at
                            ORDER BY effective_from DESC LIMIT 1
                        ) p ON true
                        WHERE u.chat_all > 0
                        UNION ALL
                        SELECT u.tenant_id, 'embedding', u.embedding_model,
                               u.emb, p.id IS NOT NULL,
                               u.emb * p.input_usd_per_million / 1000000
                        FROM u
                        LEFT JOIN LATERAL (
                            SELECT id, input_usd_per_million
                            FROM ai_model_prices
                            WHERE model_name = u.embedding_model AND effective_from <= u.occurred_at
                            ORDER BY effective_from DESC LIMIT 1
                        ) p ON true
                        WHERE u.emb > 0
                    )
                    SELECT tenant_id, kind, model, sum(tokens),
                           coalesce(sum(tokens) FILTER (WHERE NOT priced), 0),
                           coalesce(sum(cost), 0)
                    FROM lines GROUP BY tenant_id, kind, model
                    """
                ),
                self._params(since=since, ingestion=INGESTION_CHANNEL),
            )
        ).all()
        return [
            UsageCostLine(
                tenant_id=UUID(str(r[0])),
                kind=str(r[1]),
                model=r[2],
                tokens=int(r[3]),
                unpriced_tokens=int(r[4]),
                cost_usd=Decimal(r[5]),
            )
            for r in rows
        ]

    async def knowledge_summary(self) -> KnowledgeSummary:
        row = (
            await self._session.execute(
                text(
                    f"""
                    SELECT count(*) FILTER (WHERE status = 'ready'),
                           count(*) FILTER (WHERE status = 'processing'),
                           count(*) FILTER (WHERE status = 'failed'),
                           count(*) FILTER (WHERE source_url IS NOT NULL),
                           count(*) FILTER (WHERE source_url IS NULL),
                           max(created_at)
                    FROM documents
                    WHERE deleted_at IS NULL{self._and()}
                    """
                ),
                self._params(),
            )
        ).one()
        return KnowledgeSummary(
            ready=int(row[0]),
            processing=int(row[1]),
            failed=int(row[2]),
            web_pages=int(row[3]),
            files=int(row[4]),
            last_added_at=row[5],
        )
