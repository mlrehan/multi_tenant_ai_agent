"""Tenant-authorization ports, including ``TenantUnitOfWork`` -- the
RLS-subject (``app_tenant``) transaction boundary for every tenant-scoped
authorization operation. ``RoleHierarchyRepository`` and
``AuthorizationOverrideRepository`` are defined here (not duplicated) and
reused by ``platform_authz`` for the platform-scope rows of those same two
tables -- mirrors the precedent in ``domain.platform_authz.policies``
importing ``OverrideEffect`` from ``domain.tenant_authz.entities``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from types import TracebackType
from typing import Protocol
from uuid import UUID

from iam_platform.application.identity.ports import AuditWriter, SecurityEventWriter
from iam_platform.application.tenancy.ports import (
    TenantFeatureRepository,
    TenantInvitationRepository,
    TenantMembershipRepository,
)
from iam_platform.domain.tenant_authz.entities import (
    AuthorizationOverride,
    OverrideScope,
    RoleHierarchyEdge,
    RoleScope,
    TenantMembershipRole,
    TenantPermission,
    TenantRole,
)


class TenantRoleRepository(Protocol):
    async def get_by_id(self, role_id: UUID) -> TenantRole | None: ...
    async def get_by_code(self, tenant_id: UUID | None, code: str) -> TenantRole | None:
        """``tenant_id=None`` looks up a system role, shared across all tenants."""
        ...

    async def list_available_to_tenant(self, tenant_id: UUID) -> list[TenantRole]:
        """System roles (``tenant_id IS NULL``) plus this tenant's custom roles."""
        ...

    async def add(self, role: TenantRole) -> None: ...
    async def save(self, role: TenantRole) -> None: ...


class TenantPermissionRepository(Protocol):
    async def get_by_id(self, permission_id: UUID) -> TenantPermission | None: ...
    async def get_by_code(self, code: str) -> TenantPermission | None: ...
    async def list_by_codes(self, codes: set[str]) -> list[TenantPermission]: ...
    async def list_all(self) -> list[TenantPermission]:
        """The full tenant permission catalog -- not tenant-scoped, since
        `tenant_permissions` is a global reference table (docs/14-schema-
        tenant-authorization.md). Used by role/override-creation pickers."""
        ...
    async def get_role_permission_codes(self, role_ids: set[UUID]) -> dict[UUID, set[str]]: ...
    async def assign_to_role(self, *, role_id: UUID, permission_code: str, now: datetime) -> None: ...
    async def revoke_from_role(self, *, role_id: UUID, permission_code: str) -> None: ...


class TenantMembershipRoleRepository(Protocol):
    async def list_active_by_membership(self, membership_id: UUID) -> list[TenantMembershipRole]: ...
    async def get_active(
        self, *, membership_id: UUID, role_id: UUID
    ) -> TenantMembershipRole | None: ...
    async def add(self, assignment: TenantMembershipRole) -> None: ...
    async def save(self, assignment: TenantMembershipRole) -> None: ...


class RoleHierarchyRepository(Protocol):
    async def list_edges_by_parent(
        self, *, scope: RoleScope, tenant_id: UUID | None
    ) -> dict[UUID, list[UUID]]:
        """Returns ``{parent_role_id: [child_role_id, ...]}`` for every edge
        in this scope (and, for tenant scope, this tenant's custom-role
        edges plus system-role edges)."""
        ...

    async def add(self, edge: RoleHierarchyEdge) -> None: ...


class AuthorizationOverrideRepository(Protocol):
    async def list_active_for_subject(
        self, *, scope: OverrideScope, tenant_id: UUID | None, subject_id: UUID, now: datetime
    ) -> list[AuthorizationOverride]: ...

    async def add(self, override: AuthorizationOverride) -> None: ...
    async def revoke(self, override_id: UUID, *, now: datetime) -> None: ...


class TenantUnitOfWork(Protocol):
    """Opened with a verified ``tenant_id`` (and the authenticated
    ``user_id``) -- ``__aenter__`` issues ``SET LOCAL app.tenant_id`` /
    ``app.user_id`` so every repository call is subject to the tenant's RLS
    policies. See docs/18-schema-rls-and-migrations.md."""

    tenant_memberships: TenantMembershipRepository
    tenant_invitations: TenantInvitationRepository
    tenant_features: TenantFeatureRepository
    tenant_roles: TenantRoleRepository
    tenant_permissions: TenantPermissionRepository
    tenant_membership_roles: TenantMembershipRoleRepository
    role_hierarchy: RoleHierarchyRepository
    authorization_overrides: AuthorizationOverrideRepository
    audit: AuditWriter
    security_events: SecurityEventWriter

    async def __aenter__(self) -> TenantUnitOfWork: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...


# (user_id, tenant_id) -> a UoW bound to that context; `tenant_id=None` opens
# a "self-lookup only" transaction (SET LOCAL app.user_id but not
# app.tenant_id) -- the only RLS-legal read in that state is "my own
# membership rows" via the self-lookup policy exception on
# tenant_memberships (docs/18-schema-rls-and-migrations.md). Used by
# `SelectActiveTenant` to resolve which tenant to activate before a
# tenant_id is known at all. Any write, or any read of a table without that
# exception, correctly fails under RLS in this state.
TenantUowFactory = Callable[[UUID, UUID | None], TenantUnitOfWork]
