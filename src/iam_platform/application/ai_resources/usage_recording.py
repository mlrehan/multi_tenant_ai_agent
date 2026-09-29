"""Recording embedding-only spend: ingestion, and the retrieval tester.

One implementation for both, because the rules are the same and have to stay
the same:

* **Recorded after the work, whatever happened to it.** The provider bills an
  embedding the moment it returns one. A document that then fails to index,
  or a search whose vector-store step errors, still cost what it cost.
* **All of the row's input is embedding.** There is no chat model involved,
  so `embedding_tokens` is the whole input, whatever the adapter did or did
  not report separately. That is what lets the cost be priced at the
  embedding model's rate.
* **Fails open.** The work's outcome is already decided; an error here would
  only replace it with a bookkeeping one. Neither channel is part of an
  enforced allowance, so a lost row is a slightly-low dashboard figure, never
  an uncapped limit.
"""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from iam_platform.application.ai_resources.ports import TokenUsage, UsageEvent, UsageLedger

logger = logging.getLogger(__name__)


async def record_embedding_usage(
    ledger: UsageLedger | None,
    *,
    tenant_id: UUID,
    channel: str,
    usage: TokenUsage,
    occurred_at: datetime,
) -> None:
    """One ledger row for one piece of embedding-only work. Never raises."""
    if ledger is None or usage.billable <= 0:
        return
    try:
        await ledger.record(
            UsageEvent(
                tenant_id=tenant_id,
                channel=channel,
                model_configuration_id=None,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                total_tokens=usage.billable,
                occurred_at=occurred_at,
                embedding_tokens=usage.input_tokens,
                embedding_model=usage.embedding_model,
            )
        )
    except Exception:
        logger.exception("%s usage could not be recorded for tenant %s", channel, tenant_id)
