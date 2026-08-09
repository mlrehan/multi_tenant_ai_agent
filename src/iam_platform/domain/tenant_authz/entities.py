"""Tenant authorization domain entities -- see docs/14-schema-tenant-authorization.md."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from iam_platform.domain.shared.entity import Entity


@dataclass(kw_only=True)
class TenantRole(Entity):
    tenant_id: UUID | None  # None => system role, available to every tenant
    code: str
    name: str
    description: str | None = None
    is_system: bool = False
    rank: int
    created_by_user_id: UUID | None = None
    created_at: datetime
    updated_at: datetime

    @property
    def is_custom(self) -> bool:
        return self.tenant_id is not None


@dataclass(kw_only=True)
class TenantPermission(Entity):
    code: str
    resource: str
    action: str
    description: str | None = None
    risk_level: str = "low"
    is_system: bool = True
    tenant_customizable: bool = False
    required_feature: str | None = None
    created_at: datetime


@dataclass(kw_only=True)
class TenantMembershipRole(Entity):
    tenant_id: UUID
    membership_id: UUID
    role_id: UUID
    granted_by_user_id: UUID
    granted_at: datetime
    revoked_at: datetime | None = None

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None

    def revoke(self, *, now: datetime) -> None:
        self.revoked_at = now


class RoleScope(StrEnum):
    PLATFORM = "platform"
    TENANT = "tenant"


@dataclass(kw_only=True)
class RoleHierarchyEdge(Entity):
    parent_role_id: UUID
    child_role_id: UUID
    role_scope: RoleScope
    # None for platform-scope edges and for tenant-scope edges meant to apply
    # globally (neither exists yet in practice -- every tenant-scope edge
    # created via CreateRoleHierarchyEdge sets this to the acting tenant, so
    # a tenant admin linking two roles only affects their own tenant's
    # inheritance view, never other tenants').
    tenant_id: UUID | None = None
    created_at: datetime


class OverrideScope(StrEnum):
    PLATFORM = "platform"
    TENANT = "tenant"


class OverrideSubjectType(StrEnum):
    MEMBERSHIP = "membership"
    PLATFORM_USER = "platform_user"


class OverrideEffect(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


@dataclass(kw_only=True)
class AuthorizationOverride(Entity):
    scope: OverrideScope
    tenant_id: UUID | None  # required iff scope == TENANT
    subject_type: OverrideSubjectType
    subject_id: UUID
    platform_permission_id: UUID | None = None
    tenant_permission_id: UUID | None = None
    effect: OverrideEffect
    resource_type: str | None = None
    resource_id: UUID | None = None
    reason: str
    created_by_user_id: UUID
    expires_at: datetime | None = None
    created_at: datetime
    revoked_at: datetime | None = None

    def is_active(self, *, now: datetime) -> bool:
        if self.revoked_at is not None:
            return False
        return self.expires_at is None or now < self.expires_at
