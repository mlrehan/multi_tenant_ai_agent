"""In-memory fakes for the tenant_authz and platform_authz ports -- mirrors
``tests/unit/identity/fakes.py``'s shape and rollback-simulation discipline
so use cases in both scopes can be unit-tested without a real database.

Shared between ``tests/unit/tenant_authz``, ``tests/unit/platform_authz``,
and ``tests/unit/tenancy`` (imported, not duplicated) since
``TenantUnitOfWork``/``PlatformUnitOfWork`` themselves share several
repository ports (see ``application/platform_authz/ports.py`` module
docstring).
"""

from __future__ import annotations

import copy
from datetime import datetime
from uuid import UUID, uuid4

from iam_platform.application.identity.ports import AuditWriter, SecurityEventWriter
from iam_platform.application.tenancy.ports import (
    InvitationEmailSender,
    TenantFeatureRepository,
    TenantInvitationRepository,
    TenantMembershipRepository,
    TenantRepository,
)
from iam_platform.application.tenant_authz.ports import (
    AuthorizationOverrideRepository,
    RoleHierarchyRepository,
    TenantMembershipRoleRepository,
    TenantPermissionRepository,
    TenantRoleRepository,
)
from iam_platform.domain.ai_resources.entities import ModelConfiguration
from iam_platform.domain.platform_authz.entities import (
    ImpersonationSession,
    PlatformPermission,
    PlatformRole,
    PlatformUserRole,
)
from iam_platform.domain.tenancy.entities import Tenant, TenantInvitation, TenantMembership
from iam_platform.domain.tenant_authz.entities import (
    AuthorizationOverride,
    OverrideScope,
    RoleHierarchyEdge,
    RoleScope,
    TenantMembershipRole,
    TenantPermission,
    TenantRole,
)


class FakeTenantRepository(TenantRepository):
    def __init__(self) -> None:
        self.by_id: dict[UUID, Tenant] = {}

    async def get_by_id(self, tenant_id: UUID) -> Tenant | None:
        return self.by_id.get(tenant_id)

    async def get_by_slug(self, slug: str) -> Tenant | None:
        return next((t for t in self.by_id.values() if t.slug == slug), None)

    async def list_all(self) -> list[Tenant]:
        return sorted(self.by_id.values(), key=lambda t: t.created_at, reverse=True)

    async def add(self, tenant: Tenant) -> None:
        self.by_id[tenant.id] = tenant

    async def save(self, tenant: Tenant) -> None:
        self.by_id[tenant.id] = tenant


class FakeTenantMembershipRepository(TenantMembershipRepository):
    def __init__(self) -> None:
        self.by_id: dict[UUID, TenantMembership] = {}

    async def get_by_id(self, membership_id: UUID) -> TenantMembership | None:
        return self.by_id.get(membership_id)

    async def get_by_tenant_and_user(self, tenant_id: UUID, user_id: UUID) -> TenantMembership | None:
        return next(
            (m for m in self.by_id.values() if m.tenant_id == tenant_id and m.user_id == user_id),
            None,
        )

    async def list_by_tenant(self, tenant_id: UUID) -> list[TenantMembership]:
        return [m for m in self.by_id.values() if m.tenant_id == tenant_id]

    async def list_by_user(self, user_id: UUID) -> list[TenantMembership]:
        return [m for m in self.by_id.values() if m.user_id == user_id]

    async def add(self, membership: TenantMembership) -> None:
        self.by_id[membership.id] = membership

    async def save(self, membership: TenantMembership) -> None:
        self.by_id[membership.id] = membership


class FakeTenantInvitationRepository(TenantInvitationRepository):
    def __init__(self) -> None:
        self.by_id: dict[UUID, TenantInvitation] = {}

    async def get_by_token_hash(self, token_hash: str) -> TenantInvitation | None:
        return next((i for i in self.by_id.values() if i.token_hash == token_hash), None)

    async def get_pending_by_tenant_and_email(
        self, tenant_id: UUID, email: str
    ) -> TenantInvitation | None:
        return next(
            (
                i
                for i in self.by_id.values()
                if i.tenant_id == tenant_id
                and str(i.email).lower() == email.lower()
                and i.status == "pending"
            ),
            None,
        )

    async def add(self, invitation: TenantInvitation) -> None:
        self.by_id[invitation.id] = invitation

    async def save(self, invitation: TenantInvitation) -> None:
        self.by_id[invitation.id] = invitation


class FakeTenantFeatureRepository(TenantFeatureRepository):
    def __init__(self) -> None:
        self.enabled: dict[UUID, set[str]] = {}

    async def list_enabled_codes(self, tenant_id: UUID) -> set[str]:
        return set(self.enabled.get(tenant_id, set()))

    async def enable(self, tenant_id: UUID, feature_code: str, *, now: datetime) -> None:
        self.enabled.setdefault(tenant_id, set()).add(feature_code)


class FakeTenantRoleRepository(TenantRoleRepository):
    def __init__(self) -> None:
        self.by_id: dict[UUID, TenantRole] = {}

    async def get_by_id(self, role_id: UUID) -> TenantRole | None:
        return self.by_id.get(role_id)

    async def get_by_code(self, tenant_id: UUID | None, code: str) -> TenantRole | None:
        return next(
            (r for r in self.by_id.values() if r.tenant_id == tenant_id and r.code == code), None
        )

    async def list_available_to_tenant(self, tenant_id: UUID) -> list[TenantRole]:
        return [r for r in self.by_id.values() if r.tenant_id is None or r.tenant_id == tenant_id]

    async def add(self, role: TenantRole) -> None:
        self.by_id[role.id] = role

    async def save(self, role: TenantRole) -> None:
        self.by_id[role.id] = role


class FakeTenantPermissionRepository(TenantPermissionRepository):
    def __init__(self) -> None:
        self.by_id: dict[UUID, TenantPermission] = {}
        self.role_permission_codes: dict[UUID, set[str]] = {}

    async def get_by_id(self, permission_id: UUID) -> TenantPermission | None:
        return self.by_id.get(permission_id)

    async def get_by_code(self, code: str) -> TenantPermission | None:
        return next((p for p in self.by_id.values() if p.code == code), None)

    async def list_by_codes(self, codes: set[str]) -> list[TenantPermission]:
        return [p for p in self.by_id.values() if p.code in codes]

    async def list_all(self) -> list[TenantPermission]:
        return sorted(self.by_id.values(), key=lambda p: p.code)

    async def get_role_permission_codes(self, role_ids: set[UUID]) -> dict[UUID, set[str]]:
        return {rid: set(self.role_permission_codes.get(rid, set())) for rid in role_ids}

    async def assign_to_role(self, *, role_id: UUID, permission_code: str, now: datetime) -> None:
        self.role_permission_codes.setdefault(role_id, set()).add(permission_code)

    async def revoke_from_role(self, *, role_id: UUID, permission_code: str) -> None:
        self.role_permission_codes.get(role_id, set()).discard(permission_code)


class FakeTenantMembershipRoleRepository(TenantMembershipRoleRepository):
    def __init__(self) -> None:
        self.by_id: dict[UUID, TenantMembershipRole] = {}

    async def list_active_by_membership(self, membership_id: UUID) -> list[TenantMembershipRole]:
        return [
            a for a in self.by_id.values() if a.membership_id == membership_id and a.is_active
        ]

    async def get_active(
        self, *, membership_id: UUID, role_id: UUID
    ) -> TenantMembershipRole | None:
        return next(
            (
                a
                for a in self.by_id.values()
                if a.membership_id == membership_id and a.role_id == role_id and a.is_active
            ),
            None,
        )

    async def add(self, assignment: TenantMembershipRole) -> None:
        self.by_id[assignment.id] = assignment

    async def save(self, assignment: TenantMembershipRole) -> None:
        self.by_id[assignment.id] = assignment


class FakeRoleHierarchyRepository(RoleHierarchyRepository):
    def __init__(self) -> None:
        self.edges: list[RoleHierarchyEdge] = []

    async def list_edges_by_parent(
        self, *, scope: RoleScope, tenant_id: UUID | None
    ) -> dict[UUID, list[UUID]]:
        result: dict[UUID, list[UUID]] = {}
        for edge in self.edges:
            if edge.role_scope != scope:
                continue
            if scope == RoleScope.TENANT and edge.tenant_id not in (None, tenant_id):
                continue
            result.setdefault(edge.parent_role_id, []).append(edge.child_role_id)
        return result

    async def add(self, edge: RoleHierarchyEdge) -> None:
        self.edges.append(edge)


class FakeAuthorizationOverrideRepository(AuthorizationOverrideRepository):
    def __init__(self) -> None:
        self.by_id: dict[UUID, AuthorizationOverride] = {}

    async def list_active_for_subject(
        self, *, scope: OverrideScope, tenant_id: UUID | None, subject_id: UUID, now: datetime
    ) -> list[AuthorizationOverride]:
        return [
            o
            for o in self.by_id.values()
            if o.scope == scope
            and o.tenant_id == tenant_id
            and o.subject_id == subject_id
            and o.is_active(now=now)
        ]

    async def add(self, override: AuthorizationOverride) -> None:
        self.by_id[override.id] = override

    async def revoke(self, override_id: UUID, *, now: datetime) -> None:
        override = self.by_id.get(override_id)
        if override is not None:
            override.revoked_at = now


class FakePlatformRoleRepository:
    def __init__(self) -> None:
        self.by_id: dict[UUID, PlatformRole] = {}

    async def get_by_id(self, role_id: UUID) -> PlatformRole | None:
        return self.by_id.get(role_id)

    async def get_by_code(self, code: str) -> PlatformRole | None:
        return next((r for r in self.by_id.values() if r.code == code), None)

    async def list_all(self) -> list[PlatformRole]:
        return list(self.by_id.values())

    async def add(self, role: PlatformRole) -> None:
        self.by_id[role.id] = role

    async def save(self, role: PlatformRole) -> None:
        self.by_id[role.id] = role


class FakePlatformPermissionRepository:
    def __init__(self) -> None:
        self.by_id: dict[UUID, PlatformPermission] = {}
        self.role_permission_codes: dict[UUID, set[str]] = {}

    async def get_by_id(self, permission_id: UUID) -> PlatformPermission | None:
        return self.by_id.get(permission_id)

    async def get_by_code(self, code: str) -> PlatformPermission | None:
        return next((p for p in self.by_id.values() if p.code == code), None)

    async def list_all(self) -> list[PlatformPermission]:
        return sorted(self.by_id.values(), key=lambda p: p.code)

    async def get_role_permission_codes(self, role_ids: set[UUID]) -> dict[UUID, set[str]]:
        return {rid: set(self.role_permission_codes.get(rid, set())) for rid in role_ids}

    async def assign_to_role(self, *, role_id: UUID, permission_code: str) -> None:
        self.role_permission_codes.setdefault(role_id, set()).add(permission_code)

    async def revoke_from_role(self, *, role_id: UUID, permission_code: str) -> None:
        self.role_permission_codes.get(role_id, set()).discard(permission_code)


class FakePlatformUserRoleRepository:
    def __init__(self) -> None:
        self.by_id: dict[UUID, PlatformUserRole] = {}

    async def list_active_by_user(self, user_id: UUID) -> list[PlatformUserRole]:
        return [a for a in self.by_id.values() if a.user_id == user_id and a.is_active]

    async def get_active(self, *, user_id: UUID, role_id: UUID) -> PlatformUserRole | None:
        return next(
            (
                a
                for a in self.by_id.values()
                if a.user_id == user_id and a.role_id == role_id and a.is_active
            ),
            None,
        )

    async def add(self, assignment: PlatformUserRole) -> None:
        self.by_id[assignment.id] = assignment

    async def save(self, assignment: PlatformUserRole) -> None:
        self.by_id[assignment.id] = assignment


class FakeImpersonationSessionRepository:
    def __init__(self) -> None:
        self.by_id: dict[UUID, ImpersonationSession] = {}

    async def get_by_id(self, session_id: UUID) -> ImpersonationSession | None:
        return self.by_id.get(session_id)

    async def get_active_for_platform_user(
        self, platform_user_id: UUID, *, now: datetime
    ) -> ImpersonationSession | None:
        return next(
            (
                s
                for s in self.by_id.values()
                if s.platform_user_id == platform_user_id and s.is_active(now=now)
            ),
            None,
        )

    async def add(self, session: ImpersonationSession) -> None:
        self.by_id[session.id] = session

    async def save(self, session: ImpersonationSession) -> None:
        self.by_id[session.id] = session


class FakeAuditWriter(AuditWriter):
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def record(self, **kwargs: object) -> None:
        self.events.append(kwargs)


class FakeSecurityEventWriter(SecurityEventWriter):
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def record(self, **kwargs: object) -> None:
        self.events.append(kwargs)


class FakeInvitationEmailSender(InvitationEmailSender):
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    async def send_invitation_email(self, *, to: str, token: str, tenant_name: str) -> None:
        self.sent.append((to, token, tenant_name))



class FakeTenantEntitlementRepository:
    """Permissive by default: these tests are not about plans.

    Defined here rather than imported from `tests.unit.ai_resources.fakes` --
    that module already imports from this one, and reaching back the other way
    is a genuine import cycle, not a style preference.

    Standing in for a generously provisioned tenant keeps tests that never
    configured a limit from failing on one. A test about entitlements assigns
    `stored[tenant_id]` explicitly.
    """

    def __init__(self) -> None:
        self.stored: dict[UUID, object] = {}

    async def get_for_tenant(self, tenant_id: UUID) -> object | None:
        if tenant_id in self.stored:
            return self.stored[tenant_id]
        from datetime import UTC, datetime
        from uuid import uuid4

        from iam_platform.domain.tenancy.entitlements import TenantEntitlements

        now = datetime(2026, 1, 1, tzinfo=UTC)
        return TenantEntitlements(
            id=uuid4(),
            tenant_id=tenant_id,
            max_knowledge_bases=None,
            max_chat_widgets=None,
            max_messages_per_day=None,
            max_tokens_per_month=None,
            allow_invite_members=True,
            allow_create_roles=True,
            created_at=now,
            updated_at=now,
        )

    async def upsert(self, entitlements: object) -> None:
        self.stored[entitlements.tenant_id] = entitlements  # type: ignore[attr-defined]

    async def list_all(self) -> list[object]:
        return list(self.stored.values())

    async def count_knowledge_bases(self, tenant_id: UUID) -> int:
        return 0

    async def count_chat_widgets(self, tenant_id: UUID) -> int:
        return 0

    async def count_assistants(self, tenant_id: UUID) -> int:
        return 0

class FakeTenantUnitOfWork:
    """Shared instance doubles as its own factory (``self(user_id, tenant_id)``
    returns itself, matching ``TenantUowFactory``'s call shape) so tests can
    inspect state after the use case's ``async with`` block finishes.

    Simulates real rollback-on-raise like ``FakeIdentityUnitOfWork`` -- see
    that class's docstring for why a no-op fake would hide the exact class of
    bug the real ``SqlTenantUnitOfWork`` had to be fixed for.
    """

    _REPO_ATTRS = (
        "tenant_memberships",
        "tenant_invitations",
        "tenant_features",
        "tenant_roles",
        "tenant_permissions",
        "tenant_membership_roles",
        "role_hierarchy",
        "authorization_overrides",
        "audit",
        "security_events",
    )

    def __init__(self) -> None:
        self.tenant_memberships = FakeTenantMembershipRepository()
        self.tenant_invitations = FakeTenantInvitationRepository()
        self.tenant_features = FakeTenantFeatureRepository()
        self.tenant_roles = FakeTenantRoleRepository()
        self.tenant_permissions = FakeTenantPermissionRepository()
        self.tenant_membership_roles = FakeTenantMembershipRoleRepository()
        self.role_hierarchy = FakeRoleHierarchyRepository()
        self.authorization_overrides = FakeAuthorizationOverrideRepository()
        self.entitlements = FakeTenantEntitlementRepository()
        self.tenant_entitlements = self.entitlements
        self.audit = FakeAuditWriter()
        self.security_events = FakeSecurityEventWriter()
        self.last_user_id: UUID | None = None
        self.last_tenant_id: UUID | None = None
        self._snapshot: dict[str, object] | None = None

    def __call__(self, user_id: UUID, tenant_id: UUID | None) -> FakeTenantUnitOfWork:
        self.last_user_id = user_id
        self.last_tenant_id = tenant_id
        return self

    async def __aenter__(self) -> FakeTenantUnitOfWork:
        self._snapshot = {name: copy.deepcopy(getattr(self, name)) for name in self._REPO_ATTRS}
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        if exc_type is not None:
            assert self._snapshot is not None
            for name, value in self._snapshot.items():
                setattr(self, name, value)
        self._snapshot = None


class FakePlatformModelConfigurationRepository:
    def __init__(self) -> None:
        self.by_id: dict[UUID, ModelConfiguration] = {}

    async def get_by_id(self, model_configuration_id: UUID) -> ModelConfiguration | None:
        return self.by_id.get(model_configuration_id)

    async def list_all(self, *, include_archived: bool = True) -> list[ModelConfiguration]:
        return [
            m
            for m in self.by_id.values()
            if include_archived or not m.is_archived
        ]

    async def add(self, model_configuration: ModelConfiguration) -> None:
        self.by_id[model_configuration.id] = model_configuration

    async def save(self, model_configuration: ModelConfiguration) -> None:
        self.by_id[model_configuration.id] = model_configuration


class FakeTenantModelAccessRepository:
    """Grants, plus the assistants that block a revocation.

    `blocking_assistants` stands in for the `ai_assistants` rows the real
    repository counts. Modelled rather than ignored because "revocation is
    refused while an assistant depends on it" is the policy under test, and a
    fake that always allowed revocation would make that test vacuous.
    """

    def __init__(self, configurations: FakePlatformModelConfigurationRepository) -> None:
        self._configurations = configurations
        self.grants: set[tuple[UUID, UUID]] = set()
        self.blocking_assistants: dict[tuple[UUID, UUID], int] = {}

    async def list_tenant_ids_for_configuration(
        self, model_configuration_id: UUID
    ) -> list[UUID]:
        return [t for (t, c) in self.grants if c == model_configuration_id]

    async def grant(
        self, *, tenant_id: UUID, model_configuration_id: UUID, granted_by_user_id: UUID
    ) -> None:
        self.grants.add((tenant_id, model_configuration_id))

    async def revoke(self, *, tenant_id: UUID, model_configuration_id: UUID) -> int:
        blocking = self.blocking_assistants.get((tenant_id, model_configuration_id), 0)
        if blocking:
            return blocking
        self.grants.discard((tenant_id, model_configuration_id))
        return 0


class FakePlatformUnitOfWork:
    """Same shared-instance-as-factory + rollback-simulation pattern as
    ``FakeTenantUnitOfWork``, matching ``PlatformUowFactory``'s call shape."""

    _REPO_ATTRS = (
        "platform_roles",
        "platform_permissions",
        "platform_user_roles",
        "tenants",
        "tenant_memberships",
        "tenant_roles",
        "tenant_membership_roles",
        "role_hierarchy",
        "authorization_overrides",
        "impersonation_sessions",
        "model_configurations",
        "tenant_model_access",
        "audit",
        "security_events",
    )

    def __init__(self) -> None:
        self.platform_roles = FakePlatformRoleRepository()
        self.platform_permissions = FakePlatformPermissionRepository()
        self.platform_user_roles = FakePlatformUserRoleRepository()
        self.tenants = FakeTenantRepository()
        self.tenant_memberships = FakeTenantMembershipRepository()
        self.tenant_roles = FakeTenantRoleRepository()
        self.tenant_membership_roles = FakeTenantMembershipRoleRepository()
        self.role_hierarchy = FakeRoleHierarchyRepository()
        self.authorization_overrides = FakeAuthorizationOverrideRepository()
        self.impersonation_sessions = FakeImpersonationSessionRepository()
        self.model_configurations = FakePlatformModelConfigurationRepository()
        self.tenant_model_access = FakeTenantModelAccessRepository(self.model_configurations)
        self.entitlements = FakeTenantEntitlementRepository()
        self.tenant_entitlements = self.entitlements
        self.audit = FakeAuditWriter()
        self.security_events = FakeSecurityEventWriter()
        self.last_user_id: UUID | None = None
        self._snapshot: dict[str, object] | None = None

    def __call__(self, user_id: UUID) -> FakePlatformUnitOfWork:
        self.last_user_id = user_id
        return self

    async def __aenter__(self) -> FakePlatformUnitOfWork:
        self._snapshot = {name: copy.deepcopy(getattr(self, name)) for name in self._REPO_ATTRS}
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        if exc_type is not None:
            assert self._snapshot is not None
            for name, value in self._snapshot.items():
                setattr(self, name, value)
        self._snapshot = None


def make_tenant_role(
    *, tenant_id: UUID | None, code: str, rank: int, now: datetime, is_system: bool = False
) -> TenantRole:
    return TenantRole(
        id=uuid4(),
        tenant_id=tenant_id,
        code=code,
        name=code,
        is_system=is_system,
        rank=rank,
        created_at=now,
        updated_at=now,
    )


def make_platform_role(
    *, code: str, rank: int, now: datetime, is_system: bool = False
) -> PlatformRole:
    # `PlatformRole.is_system` defaults to True on the domain entity itself
    # (mirroring how every seeded platform role is system-defined) -- this
    # fixture defaults the *other* way, since most callers build a role to
    # test grant/revoke or custom-role editing against, where `is_system=True`
    # would trip the immutable-system-role guard unexpectedly.
    return PlatformRole(
        id=uuid4(), code=code, name=code, is_system=is_system, rank=rank, created_at=now, updated_at=now
    )


def make_tenant_permission(*, code: str, now: datetime, tenant_customizable: bool = True) -> TenantPermission:
    return TenantPermission(
        id=uuid4(),
        code=code,
        resource=code.split(".")[0],
        action=code.split(".")[-1],
        tenant_customizable=tenant_customizable,
        created_at=now,
    )


def make_platform_permission(*, code: str, now: datetime) -> PlatformPermission:
    return PlatformPermission(
        id=uuid4(), code=code, resource=code.split(".")[0], action=code.split(".")[-1], created_at=now
    )
