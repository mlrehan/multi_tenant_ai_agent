"""The cost arithmetic, kept free of use cases and ports.

Pure on purpose: the platform overview and the price-list use case both need
it, and the overview is imported by the price list -- so this lives below both.
See `usage_costs` for what the figures mean and what they do not include.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from iam_platform.application.ai_resources.ports import UsageCostLine
from iam_platform.domain.ai_resources.pricing import ModelPrice

ZERO = Decimal(0)


@dataclass(frozen=True, slots=True)
class ModelCost:
    model: str | None
    #: "chat" or "embedding" -- one model name can in principle appear as both.
    kind: str
    tokens: int
    unpriced_tokens: int
    cost_usd: Decimal


@dataclass(frozen=True, slots=True)
class TenantCost:
    cost_usd: Decimal
    unpriced_tokens: int


@dataclass(frozen=True, slots=True)
class CostSummary:
    total_usd: Decimal = ZERO
    priced_tokens: int = 0
    unpriced_tokens: int = 0
    #: Most expensive first; unpriced models after the priced ones.
    by_model: list[ModelCost] = field(default_factory=list)
    by_tenant: dict[UUID, TenantCost] = field(default_factory=dict)


def summarise_costs(lines: list[UsageCostLine]) -> CostSummary:
    """Totals per model and per tenant. Pure, so the arithmetic is tested
    without a database; the per-row pricing itself happens in SQL."""
    per_model: dict[tuple[str | None, str], list[int | Decimal]] = defaultdict(
        lambda: [0, 0, ZERO]
    )
    per_tenant: dict[UUID, list[int | Decimal]] = defaultdict(lambda: [ZERO, 0])
    total, priced, unpriced = ZERO, 0, 0
    for line in lines:
        m = per_model[(line.model, line.kind)]
        m[0] = int(m[0]) + line.tokens
        m[1] = int(m[1]) + line.unpriced_tokens
        m[2] = Decimal(m[2]) + line.cost_usd
        t = per_tenant[line.tenant_id]
        t[0] = Decimal(t[0]) + line.cost_usd
        t[1] = int(t[1]) + line.unpriced_tokens
        total += line.cost_usd
        priced += line.tokens - line.unpriced_tokens
        unpriced += line.unpriced_tokens
    by_model = [
        ModelCost(
            model=model,
            kind=kind,
            tokens=int(v[0]),
            unpriced_tokens=int(v[1]),
            cost_usd=Decimal(v[2]),
        )
        for (model, kind), v in per_model.items()
    ]
    by_model.sort(key=lambda c: (c.unpriced_tokens == c.tokens, -c.cost_usd, -c.tokens))
    return CostSummary(
        total_usd=total,
        priced_tokens=priced,
        unpriced_tokens=unpriced,
        by_model=by_model,
        by_tenant={
            tenant: TenantCost(cost_usd=Decimal(v[0]), unpriced_tokens=int(v[1]))
            for tenant, v in per_tenant.items()
        },
    )


def current_price(prices: list[ModelPrice], model: str, at: datetime) -> ModelPrice | None:
    """The entry in force for `model` at `at` -- the same rule the SQL uses."""
    candidates = [p for p in prices if p.model_name == model and p.effective_from <= at]
    return max(candidates, key=lambda p: p.effective_from, default=None)
