"""Platform authorization domain entities -- see docs/12-schema-platform-authorization.md."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from iam_platform.domain.shared.entity import Entity


@dataclass(kw_only=True)
class PlatformRole(Entity):
    code: str
    name: str
    description: str | None = None
    is_system: bool = True
    rank: int
    created_at: datetime
    updated_at: datetime


@dataclass(kw_only=True)
class PlatformPermission(Entity):
    code: str
    resource: str
    action: str
    description: str | None = None
    risk_level: str = "low"
    is_system: bool = True
    created_at: datetime


@dataclass(kw_only=True)
class PlatformUserRole(Entity):
    user_id: UUID
    role_id: UUID
    granted_by_user_id: UUID
    granted_at: datetime
    revoked_at: datetime | None = None
    revoked_by_user_id: UUID | None = None

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None

    def revoke(self, *, by_user_id: UUID, now: datetime) -> None:
        self.revoked_at = now
        self.revoked_by_user_id = by_user_id


@dataclass(kw_only=True)
class ImpersonationSession(Entity):
    """Platform-initiated support access -- docs/06-authorization-model.md §5,
    docs/17-schema-security-audit.md."""

    platform_user_id: UUID
    target_user_id: UUID
    tenant_id: UUID
    reason: str
    approval_status: str = "not_required"
    approved_by_user_id: UUID | None = None
    started_at: datetime
    expires_at: datetime
    ended_at: datetime | None = None
    ip: str | None = None
    session_id: UUID | None = None

    def is_active(self, *, now: datetime) -> bool:
        return self.ended_at is None and now < self.expires_at

    def end(self, *, now: datetime) -> None:
        self.ended_at = now
