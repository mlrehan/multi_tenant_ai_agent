"""Estimated AI cost, from the usage ledger and the platform's price list.

**Estimated, and says so.** The cost is tokens times the price the platform
entered, at the moment each row occurred. It is not the provider's invoice:
cached-input discounts, batch pricing, taxes and credits are not modelled, and
a price entered wrongly is a cost reported wrongly. The console labels it an
estimate for that reason.

**Unpriced is reported, never zero.** A row whose model has no price in force
contributes its tokens to `unpriced_tokens` and nothing to the cost. A total
that silently treated those as free would understate spend, which is the one
error a cost figure must not make quietly.

Each row is priced in two parts, because one answer pays two different
models: its chat tokens at the chat model's price, and the question's
embedding at the embedding model's. Ingestion rows are embedding only. The
split relies on `embedding_tokens`, which rows before migration `e7a3c1f9d2b4`
lack -- together with their unknown model, that leaves them unpriced.

Platform-only. What the platform pays its provider is not tenant information,
so nothing here is reachable from a tenant's session.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from iam_platform.application.ai_resources.cost_summary import (
    CostSummary,
    ModelCost,
    TenantCost,
    current_price,
    summarise_costs,
)
from iam_platform.application.ai_resources.exceptions import (
    ModelConfigurationManagementDeniedError,
    ModelPriceConflictError,
    ModelPriceNotFoundError,
)
from iam_platform.application.ai_resources.platform_overview import (
    MANAGE_PERMISSION,
    month_start_utc,
)
from iam_platform.application.platform_authz.effective_permissions import (
    compute_effective_platform_state,
)
from iam_platform.application.platform_authz.ports import (
    PlatformUnitOfWork,
    PlatformUowFactory,
)
from iam_platform.core.clock import Clock
from iam_platform.domain.ai_resources.pricing import ModelPrice


async def _authorize(uow: PlatformUnitOfWork, actor_id: UUID, now: datetime) -> None:
    state = await compute_effective_platform_state(uow, actor_id, now=now)
    if MANAGE_PERMISSION not in state.permissions:
        raise ModelConfigurationManagementDeniedError(MANAGE_PERMISSION)


# -- the price list -----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ModelInUse:
    """A model this month's usage paid for, and what it currently costs."""

    model: str
    kind: str
    tokens_this_month: int
    current: ModelPrice | None


@dataclass(frozen=True, slots=True)
class ModelPriceCatalogue:
    #: Every entry, newest effective first -- the history is the audit trail
    #: of what each past cost was computed with.
    prices: list[ModelPrice]
    #: What needs a price: every model that appears in this month's usage.
    #: A price entered for a model nobody uses changes nothing on the screen.
    models_in_use: list[ModelInUse]


@dataclass(frozen=True, slots=True)
class ListModelPricesQuery:
    actor_user_id: str


class ListModelPrices:
    def __init__(self, uow_factory: PlatformUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, query: ListModelPricesQuery) -> ModelPriceCatalogue:
        actor_id = UUID(query.actor_user_id)
        now = self._clock.now()
        async with self._uow_factory(actor_id) as uow:
            await _authorize(uow, actor_id, now)
            prices = await uow.model_prices.list_all()
            lines = await uow.activity.usage_cost_lines(since=month_start_utc(now))

        tokens: dict[tuple[str, str], int] = defaultdict(int)
        for line in lines:
            if line.model:
                tokens[(line.model, line.kind)] += line.tokens
        in_use = [
            ModelInUse(
                model=model,
                kind=kind,
                tokens_this_month=count,
                current=current_price(prices, model, now),
            )
            for (model, kind), count in sorted(tokens.items(), key=lambda kv: -kv[1])
        ]
        prices.sort(key=lambda p: (p.model_name, p.effective_from), reverse=True)
        return ModelPriceCatalogue(prices=prices, models_in_use=in_use)


@dataclass(frozen=True, slots=True)
class SetModelPriceCommand:
    actor_user_id: str
    model_name: str
    input_usd_per_million: Decimal
    output_usd_per_million: Decimal
    #: None means "from now". An earlier moment prices usage already recorded
    #: -- needed the first time a price is entered, or this month's usage so
    #: far stays unpriced.
    effective_from: datetime | None = None


class SetModelPrice:
    """Adds a price entry. Never edits one: that would silently change costs
    already reported. A price change is a new entry from a later moment."""

    def __init__(self, uow_factory: PlatformUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: SetModelPriceCommand) -> UUID:
        actor_id = UUID(command.actor_user_id)
        now = self._clock.now()
        price = ModelPrice.validated(
            id=uuid4(),
            model_name=command.model_name,
            input_usd_per_million=command.input_usd_per_million,
            output_usd_per_million=command.output_usd_per_million,
            effective_from=command.effective_from or now,
            created_by_user_id=actor_id,
            created_at=now,
        )
        async with self._uow_factory(actor_id) as uow:
            await _authorize(uow, actor_id, now)
            if await uow.model_prices.exists(
                model_name=price.model_name, effective_from=price.effective_from
            ):
                raise ModelPriceConflictError(
                    f"{price.model_name} already has a price from "
                    f"{price.effective_from.isoformat()}; delete it first to replace it"
                )
            await uow.model_prices.add(price)
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=actor_id,
                tenant_id=None,
                action="platform.model_price_created",
                resource_type="model_price",
                resource_id=price.id,
                result="success",
                metadata={
                    "model_name": price.model_name,
                    "input_usd_per_million": str(price.input_usd_per_million),
                    "output_usd_per_million": str(price.output_usd_per_million),
                    "effective_from": price.effective_from.isoformat(),
                },
            )
        return price.id


@dataclass(frozen=True, slots=True)
class DeleteModelPriceCommand:
    actor_user_id: str
    price_id: str


class DeleteModelPrice:
    """Removes a mistaken entry. Audited with the values it held, so what a
    past cost was computed with stays reconstructable."""

    def __init__(self, uow_factory: PlatformUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: DeleteModelPriceCommand) -> None:
        actor_id = UUID(command.actor_user_id)
        price_id = UUID(command.price_id)
        async with self._uow_factory(actor_id) as uow:
            await _authorize(uow, actor_id, self._clock.now())
            price = await uow.model_prices.get(price_id)
            if price is None:
                raise ModelPriceNotFoundError(command.price_id)
            await uow.model_prices.delete(price_id)
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=actor_id,
                tenant_id=None,
                action="platform.model_price_deleted",
                resource_type="model_price",
                resource_id=price_id,
                result="success",
                metadata={
                    "model_name": price.model_name,
                    "input_usd_per_million": str(price.input_usd_per_million),
                    "output_usd_per_million": str(price.output_usd_per_million),
                    "effective_from": price.effective_from.isoformat(),
                },
            )


__all__ = [
    "CostSummary",
    "DeleteModelPrice",
    "DeleteModelPriceCommand",
    "ListModelPrices",
    "ListModelPricesQuery",
    "ModelCost",
    "ModelInUse",
    "ModelPriceCatalogue",
    "SetModelPrice",
    "SetModelPriceCommand",
    "TenantCost",
    "current_price",
    "summarise_costs",
]
