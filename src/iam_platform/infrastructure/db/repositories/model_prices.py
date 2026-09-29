"""The platform's model price list.

Plain SQL rather than an ORM mapping: the table is written by one use case and
read by two, and its whole contract is four statements. Runs on the platform
(BYPASSRLS) connection; `app_tenant` holds no privilege on this table at all.
There is deliberately no UPDATE -- a price is replaced by a new entry, never
edited, so a cost already reported keeps the price it was computed with.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from iam_platform.domain.ai_resources.pricing import ModelPrice

_COLUMNS = (
    "id, model_name, input_usd_per_million, output_usd_per_million, "
    "effective_from, created_by_user_id, created_at"
)


def _to_domain(row: Any) -> ModelPrice:
    return ModelPrice(
        id=UUID(str(row[0])),
        model_name=row[1],
        input_usd_per_million=Decimal(row[2]),
        output_usd_per_million=Decimal(row[3]),
        effective_from=row[4],
        created_by_user_id=UUID(str(row[5])) if row[5] is not None else None,
        created_at=row[6],
    )


class SqlModelPriceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_all(self) -> list[ModelPrice]:
        rows = (
            await self._session.execute(
                text(f"SELECT {_COLUMNS} FROM ai_model_prices ORDER BY model_name, effective_from")
            )
        ).all()
        return [_to_domain(r) for r in rows]

    async def get(self, price_id: UUID) -> ModelPrice | None:
        row = (
            await self._session.execute(
                text(f"SELECT {_COLUMNS} FROM ai_model_prices WHERE id = :id"),
                {"id": price_id},
            )
        ).first()
        return _to_domain(row) if row is not None else None

    async def exists(self, *, model_name: str, effective_from: datetime) -> bool:
        found = await self._session.scalar(
            text(
                "SELECT 1 FROM ai_model_prices "
                "WHERE model_name = :model AND effective_from = :at"
            ),
            {"model": model_name, "at": effective_from},
        )
        return found is not None

    async def add(self, price: ModelPrice) -> None:
        await self._session.execute(
            text(
                f"INSERT INTO ai_model_prices ({_COLUMNS}) "
                "VALUES (:id, :model, :input, :output, :at, :by, :created)"
            ),
            {
                "id": price.id,
                "model": price.model_name,
                "input": price.input_usd_per_million,
                "output": price.output_usd_per_million,
                "at": price.effective_from,
                "by": price.created_by_user_id,
                "created": price.created_at,
            },
        )

    async def delete(self, price_id: UUID) -> None:
        await self._session.execute(
            text("DELETE FROM ai_model_prices WHERE id = :id"), {"id": price_id}
        )
