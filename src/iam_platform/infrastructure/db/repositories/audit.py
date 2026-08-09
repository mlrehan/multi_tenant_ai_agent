"""SQLAlchemy implementations of the audit/security-event/login-attempt/lockout ports."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from iam_platform.core.ids import uuid7
from iam_platform.domain.identity.entities import AccountLockout
from iam_platform.infrastructure.db.models.audit import (
    AccountLockoutModel,
    AuditLogModel,
    LoginAttemptModel,
    SecurityEventModel,
)


class SqlAuditWriter:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        actor_user_id: UUID | None,
        effective_user_id: UUID | None,
        tenant_id: UUID | None,
        action: str,
        resource_type: str | None = None,
        resource_id: UUID | None = None,
        result: str,
        failure_reason: str | None = None,
        ip: str | None = None,
        user_agent: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._session.add(
            AuditLogModel(
                id=uuid7(),
                actor_user_id=actor_user_id,
                effective_user_id=effective_user_id,
                tenant_id=tenant_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                result=result,
                failure_reason=failure_reason,
                ip=ip,
                user_agent=user_agent,
                metadata_=metadata or {},
            )
        )
        await self._session.flush()


class SqlSecurityEventWriter:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        user_id: UUID | None,
        tenant_id: UUID | None,
        event_type: str,
        severity: str,
        details: dict[str, Any] | None = None,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        self._session.add(
            SecurityEventModel(
                id=uuid7(),
                user_id=user_id,
                tenant_id=tenant_id,
                event_type=event_type,
                severity=severity,
                details=details or {},
                ip=ip,
                user_agent=user_agent,
            )
        )
        await self._session.flush()


class SqlLoginAttemptRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        email_attempted: str,
        user_id: UUID | None,
        result: str,
        ip: str | None,
        user_agent: str | None,
        now: datetime,
    ) -> None:
        self._session.add(
            LoginAttemptModel(
                id=uuid7(),
                email_attempted=email_attempted,
                user_id=user_id,
                result=result,
                ip=ip,
                user_agent=user_agent,
            )
        )
        await self._session.flush()

    async def count_recent_failures(self, *, email: str, since: datetime) -> int:
        stmt = select(func.count()).where(
            LoginAttemptModel.email_attempted == email,
            LoginAttemptModel.result != "success",
            LoginAttemptModel.occurred_at >= since,
        )
        return (await self._session.execute(stmt)).scalar_one()


def _lockout_to_domain(m: AccountLockoutModel) -> AccountLockout:
    return AccountLockout(
        id=m.id,
        user_id=m.user_id,
        locked_at=m.locked_at,
        unlock_at=m.unlock_at,
        reason=m.reason,
        failed_attempt_count=m.failed_attempt_count,
        unlocked_by_user_id=m.unlocked_by_user_id,
        unlocked_at=m.unlocked_at,
    )


class SqlAccountLockoutRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_active(self, *, user_id: UUID, now: datetime) -> AccountLockout | None:
        stmt = (
            select(AccountLockoutModel)
            .where(AccountLockoutModel.user_id == user_id, AccountLockoutModel.unlocked_at.is_(None))
            .order_by(AccountLockoutModel.locked_at.desc())
        )
        model = (await self._session.execute(stmt)).scalars().first()
        return _lockout_to_domain(model) if model else None

    async def add(self, lockout: AccountLockout) -> None:
        self._session.add(
            AccountLockoutModel(
                id=lockout.id,
                user_id=lockout.user_id,
                locked_at=lockout.locked_at,
                unlock_at=lockout.unlock_at,
                reason=lockout.reason,
                failed_attempt_count=lockout.failed_attempt_count,
                unlocked_by_user_id=lockout.unlocked_by_user_id,
                unlocked_at=lockout.unlocked_at,
            )
        )
        await self._session.flush()
