"""What a model costs, and what a quantity of tokens therefore cost.

Prices are **entered by the platform, never assumed.** A provider's price list
changes, differs by contract, and is not something this code can know; a
built-in table would be wrong the day a price moved, and nothing would say so.
A model without an entered price is reported as *unpriced* -- a quantity of
tokens with no cost attached -- and never as costing nothing.

Money is `Decimal` throughout. Prices are per **million** tokens, as providers
publish them, and a single answer costs fractions of a cent: floats would
accumulate visible error across a month of rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from iam_platform.domain.shared.exceptions import DomainError

#: Six decimal places, the column's scale. Finer than any published price.
PRICE_QUANTUM = Decimal("0.000001")
#: A sanity ceiling, not a business rule: $10,000 per million tokens is far
#: above any real model, and a value past it is a typo (a missing decimal
#: point), which would otherwise inflate every cost on the dashboard.
MAX_USD_PER_MILLION = Decimal("10000")
MAX_MODEL_NAME_LENGTH = 200
ONE_MILLION = Decimal(1_000_000)


class ModelPriceInvalid(DomainError):
    """A price that cannot be stored as entered."""


@dataclass(frozen=True, slots=True)
class ModelPrice:
    """A model's price from `effective_from` until the next entry for it.

    Embedding models are priced on input only; their `output_usd_per_million`
    is simply never used.
    """

    id: UUID
    model_name: str
    input_usd_per_million: Decimal
    output_usd_per_million: Decimal
    effective_from: datetime
    created_by_user_id: UUID | None
    created_at: datetime

    @staticmethod
    def validated(
        *,
        id: UUID,
        model_name: str,
        input_usd_per_million: Decimal,
        output_usd_per_million: Decimal,
        effective_from: datetime,
        created_by_user_id: UUID | None,
        created_at: datetime,
    ) -> ModelPrice:
        name = model_name.strip()
        if not name:
            raise ModelPriceInvalid("a price needs the model's name, exactly as the provider spells it")
        if len(name) > MAX_MODEL_NAME_LENGTH:
            raise ModelPriceInvalid(f"a model name can be at most {MAX_MODEL_NAME_LENGTH} characters")
        if effective_from.tzinfo is None:
            raise ModelPriceInvalid("the effective time must include a time zone")
        prices = []
        for label, value in (("input", input_usd_per_million), ("output", output_usd_per_million)):
            if not value.is_finite() or value < 0:
                raise ModelPriceInvalid(f"the {label} price must be zero or more")
            if value > MAX_USD_PER_MILLION:
                raise ModelPriceInvalid(
                    f"the {label} price of ${value} per million tokens is above "
                    f"${MAX_USD_PER_MILLION}; check for a misplaced decimal point"
                )
            if value != value.quantize(PRICE_QUANTUM):
                raise ModelPriceInvalid(f"the {label} price can have at most 6 decimal places")
            prices.append(value)
        return ModelPrice(
            id=id,
            model_name=name,
            input_usd_per_million=prices[0],
            output_usd_per_million=prices[1],
            effective_from=effective_from,
            created_by_user_id=created_by_user_id,
            created_at=created_at,
        )


def cost_of(tokens: int, usd_per_million: Decimal) -> Decimal:
    """Exact cost of `tokens` at a per-million price. Not rounded: rounding is
    for display, and summing rounded parts drifts from the rounded whole."""
    return Decimal(tokens) * usd_per_million / ONE_MILLION
