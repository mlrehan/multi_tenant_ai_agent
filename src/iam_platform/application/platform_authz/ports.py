"""Platform-authorization ports, including ``PlatformUnitOfWork`` -- the
BYPASSRLS (``app_platform``) transaction boundary, used only by the small,
reviewed set of platform-scope application code (docs/20-dependency-rules.md,
docs/18-schema-rls-and-migrations.md).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from types import TracebackType
from typing import Protocol
from uuid import UUID

from iam_platform.application.ai_resources.ports import (
    TenantChatbotSettingsRepository,
    TenantEntitlementRepository,
)
from iam_platform.application.identity.ports import (
    AuditWriter,
    AuthIdentityRepository,
    CredentialRepository,
    RefreshTokenRepository,
    SecurityEventWriter,
    SessionRepository,
    UserRepository,
)
from iam_platform.application.tenancy.ports import TenantMembershipRepository, TenantRepository
from iam_platform.application.tenant_authz.ports import (
    AuthorizationOverrideRepository,
    RoleHierarchyRepository,
    TenantMembershipRoleRepository,
    TenantRoleRepository,
)
from iam_platform.domain.ai_resources.entities import ModelConfiguration
from iam_platform.domain.platform_authz.entities import (
    ImpersonationSession,
    PlatformPermission,
    PlatformRole,
    PlatformUserRole,
)


class PlatformRoleRepository(Protocol):
    async def get_by_id(self, role_id: UUID) -> PlatformRole | None: ...
    async def get_by_code(self, code: str) -> PlatformRole | None: ...
    async def list_all(self) -> list[PlatformRole]: ...
    async def add(self, role: PlatformRole) -> None: ...
    async def save(self, role: PlatformRole) -> None: ...


class PlatformPermissionRepository(Protocol):
    async def get_by_id(self, permission_id: UUID) -> PlatformPermission | None: ...
    async def get_by_code(self, code: str) -> PlatformPermission | None: ...
    async def get_role_permission_codes(self, role_ids: set[UUID]) -> dict[UUID, set[str]]: ...
    async def list_all(self) -> list[PlatformPermission]:
        """The full platform permission catalog. Used by role-picker UIs."""
        ...

    async def assign_to_role(self, *, role_id: UUID, permission_code: str) -> None: ...
    async def revoke_from_role(self, *, role_id: UUID, permission_code: str) -> None: ...


class PlatformUserRoleRepository(Protocol):
    async def list_active_by_user(self, user_id: UUID) -> list[PlatformUserRole]: ...
    async def get_active(self, *, user_id: UUID, role_id: UUID) -> PlatformUserRole | None: ...
    async def add(self, assignment: PlatformUserRole) -> None: ...
    async def save(self, assignment: PlatformUserRole) -> None: ...


class ImpersonationSessionRepository(Protocol):
    async def get_by_id(self, session_id: UUID) -> ImpersonationSession | None: ...
    async def get_active_for_platform_user(
        self, platform_user_id: UUID, *, now: datetime
    ) -> ImpersonationSession | None: ...
    async def add(self, session: ImpersonationSession) -> None: ...
    async def save(self, session: ImpersonationSession) -> None: ...


class PlatformModelConfigurationRepository(Protocol):
    """Model configurations, seen from the platform side.

    Distinct from the tenant-scoped `ModelConfigurationRepository` in
    `ai_resources.ports` for the same reason `users` is on this unit of work:
    governing them is a cross-tenant job, and the tenant-scoped connection
    deliberately cannot see across tenants.
    """

    async def get_by_id(self, model_configuration_id: UUID) -> ModelConfiguration | None: ...
    async def list_all(self, *, include_archived: bool = True) -> list[ModelConfiguration]: ...
    async def add(self, model_configuration: ModelConfiguration) -> None: ...
    async def save(self, model_configuration: ModelConfiguration) -> None: ...


class TenantModelAccessRepository(Protocol):
    """Grants of a model configuration to a tenant.

    Deletion can fail: `ai_assistants` references this table, so Postgres
    refuses to revoke a grant an assistant still depends on. `revoke` reports
    that as a value rather than letting an IntegrityError escape, so the
    caller can turn it into a 409 that explains itself.
    """

    async def list_tenant_ids_for_configuration(
        self, model_configuration_id: UUID
    ) -> list[UUID]: ...

    async def grant(
        self,
        *,
        tenant_id: UUID,
        model_configuration_id: UUID,
        granted_by_user_id: UUID,
    ) -> None:
        """Idempotent -- granting twice is not an error, it is the same state."""
        ...

    async def revoke(self, *, tenant_id: UUID, model_configuration_id: UUID) -> int:
        """Returns how many assistants block the revocation (0 == revoked)."""
        ...


class PlatformUnitOfWork(Protocol):
    platform_roles: PlatformRoleRepository
    platform_permissions: PlatformPermissionRepository
    platform_user_roles: PlatformUserRoleRepository
    # The user directory is a platform-scope read (every user, across every
    # tenant), so it belongs on the BYPASSRLS connection -- the tenant-scoped
    # IdentityUnitOfWork deliberately cannot see across tenants.
    #
    # `identities` and `credentials` are here for the same reason the two
    # tenant_authz repositories below are: administrator-created accounts need
    # a password identity written atomically with the user row, and that write
    # is gated by a platform permission, so it belongs on this connection
    # rather than requiring a second transaction on the tenant one.
    # `sessions`/`refresh_tokens` back the revoke-on-suspend/delete step --
    # without them a suspended account keeps working until its token expires.
    users: UserRepository
    identities: AuthIdentityRepository
    credentials: CredentialRepository
    sessions: SessionRepository
    refresh_tokens: RefreshTokenRepository
    tenants: TenantRepository
    tenant_memberships: TenantMembershipRepository
    # Bootstrap-only access to two tenant_authz tables (e.g. assigning the
    # system "Tenant Owner" role to a brand-new tenant's first membership,
    # atomically with tenant creation) -- BYPASSRLS makes this safe to reach
    # from the platform connection; ordinary tenant-scoped role management
    # still goes through TenantUnitOfWork.
    tenant_roles: TenantRoleRepository
    tenant_membership_roles: TenantMembershipRoleRepository
    role_hierarchy: RoleHierarchyRepository
    authorization_overrides: AuthorizationOverrideRepository
    impersonation_sessions: ImpersonationSessionRepository
    # Model-configuration governance is platform-scope by definition: the
    # platform owns the catalogue and decides which tenants may use each
    # entry, so both live on the BYPASSRLS connection.
    model_configurations: PlatformModelConfigurationRepository
    tenant_model_access: TenantModelAccessRepository
    #: Platform-written; tenants hold SELECT only on the table itself.
    tenant_entitlements: TenantEntitlementRepository
    #: Read-only here, and only for the operator dashboard's daily counts: the
    #: day a tenant's message allowance resets on lives on this row, and the
    #: platform view must resolve it exactly as the tenant's own screen does or
    #: the two show different numbers for the same day.
    chatbot_settings: TenantChatbotSettingsRepository
    audit: AuditWriter
    security_events: SecurityEventWriter

    async def __aenter__(self) -> PlatformUnitOfWork: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...


PlatformUowFactory = Callable[[UUID], PlatformUnitOfWork]
