"""Tenant creation/suspension -- platform-permission-gated per docs/01
(Platform-User vs Tenant-User Responsibility Matrix: "Create/suspend
tenants" is platform-only). Runs entirely on the BYPASSRLS platform
connection since `tenants` itself isn't tenant-owned and bootstrapping the
owner's first membership + role assignment needs to happen atomically with
tenant creation.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from iam_platform.application.platform_authz.effective_permissions import (
    compute_effective_platform_state,
)
from iam_platform.application.platform_authz.exceptions import (
    DuplicateSlugError,
    TenantCreationDeniedError,
    TenantNotFoundError,
    TenantOwnerRoleNotSeededError,
)
from iam_platform.application.platform_authz.ports import PlatformUowFactory
from iam_platform.core.clock import Clock
from iam_platform.domain.tenancy.entities import MembershipStatus, Tenant, TenantMembership, TenantStatus
from iam_platform.domain.tenant_authz.entities import TenantMembershipRole

_REQUIRED_PERMISSION = "platform.tenants.create"
_OWNER_ROLE_CODE = "tenant_owner"

# Re-exported so existing importers of these names from this module keep
# working; they now live in exceptions.py so the API's exception handlers
# actually match them (see that module's TenantCreationDeniedError docstring).
__all__ = [
    "CreateTenant",
    "CreateTenantCommand",
    "DuplicateSlugError",
    "ReactivateTenant",
    "ReactivateTenantCommand",
    "RenameTenant",
    "RenameTenantCommand",
    "SuspendTenant",
    "SuspendTenantCommand",
    "TenantCreationDeniedError",
    "TenantNotFoundError",
    "TenantOwnerRoleNotSeededError",
]


@dataclass(frozen=True, slots=True)
class CreateTenantCommand:
    actor_user_id: str
    slug: str
    display_name: str
    owner_user_id: str


class CreateTenant:
    def __init__(self, uow_factory: PlatformUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: CreateTenantCommand) -> UUID:
        actor_id = UUID(command.actor_user_id)
        owner_id = UUID(command.owner_user_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id) as uow:
            state = await compute_effective_platform_state(uow, actor_id, now=now)
            if _REQUIRED_PERMISSION not in state.permissions:
                raise TenantCreationDeniedError(_REQUIRED_PERMISSION)

            if await uow.tenants.get_by_slug(command.slug) is not None:
                raise DuplicateSlugError(command.slug)

            tenant = Tenant(
                id=uuid4(),
                slug=command.slug,
                display_name=command.display_name,
                status=TenantStatus.ACTIVE,
                owner_user_id=owner_id,
                created_at=now,
                updated_at=now,
            )
            await uow.tenants.add(tenant)

            # `ux_tenant_memberships_one_default_per_user` is a partial unique
            # index over `user_id WHERE is_default`, so only the owner's *first*
            # membership may claim the default slot. Hard-coding `is_default=True`
            # meant creating a second tenant for an owner who already belonged to
            # one raised UniqueViolationError -- a 500 on a completely ordinary
            # action. Found by creating a second tenant with the same owner.
            existing_memberships = await uow.tenant_memberships.list_by_user(owner_id)
            owner_has_default = any(m.is_default for m in existing_memberships)

            membership = TenantMembership(
                id=uuid4(),
                tenant_id=tenant.id,
                user_id=owner_id,
                status=MembershipStatus.ACTIVE,
                is_default=not owner_has_default,
                joined_at=now,
                created_at=now,
                updated_at=now,
            )
            await uow.tenant_memberships.add(membership)

            # No silent fallback here on purpose: an owner role that can't be
            # found means the tenant-scope catalog was never seeded
            # (`scripts/bootstrap_tenant_catalog.py`), and creating the
            # tenant anyway would hand back success while leaving the owner
            # with an active membership and zero permissions -- invisible
            # until someone notices the console has nothing to show them.
            owner_role = await uow.tenant_roles.get_by_code(None, _OWNER_ROLE_CODE)
            if owner_role is None:
                raise TenantOwnerRoleNotSeededError(
                    f"the {_OWNER_ROLE_CODE!r} catalog role is not seeded -- "
                    "run scripts/bootstrap_tenant_catalog.py"
                )
            await uow.tenant_membership_roles.add(
                TenantMembershipRole(
                    id=uuid4(),
                    tenant_id=tenant.id,
                    membership_id=membership.id,
                    role_id=owner_role.id,
                    granted_by_user_id=actor_id,
                    granted_at=now,
                )
            )

            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=owner_id,
                tenant_id=tenant.id,
                action="tenancy.tenant_created",
                resource_type="tenant",
                resource_id=tenant.id,
                result="success",
                metadata={"slug": command.slug},
            )
            return tenant.id


@dataclass(frozen=True, slots=True)
class SuspendTenantCommand:
    actor_user_id: str
    tenant_id: str
    reason: str


_SUSPEND_PERMISSION = "platform.tenants.suspend"


class SuspendTenant:
    def __init__(self, uow_factory: PlatformUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: SuspendTenantCommand) -> None:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id) as uow:
            state = await compute_effective_platform_state(uow, actor_id, now=now)
            if _SUSPEND_PERMISSION not in state.permissions:
                raise TenantCreationDeniedError(_SUSPEND_PERMISSION)

            tenant = await uow.tenants.get_by_id(tenant_id)
            if tenant is None:
                raise TenantNotFoundError(str(tenant_id))

            tenant.suspend(reason=command.reason, now=now)
            await uow.tenants.save(tenant)
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=None,
                tenant_id=tenant.id,
                action="tenancy.tenant_suspended",
                resource_type="tenant",
                resource_id=tenant.id,
                result="success",
                metadata={"reason": command.reason},
            )


@dataclass(frozen=True, slots=True)
class ReactivateTenantCommand:
    actor_user_id: str
    tenant_id: str


class ReactivateTenant:
    """Lifts a suspension.

    `Tenant.activate()` has existed in the domain since Phase 6 but was never
    given a use case or a route, so a tenant suspended through the API could
    not be brought back by any means short of a manual UPDATE against the
    database. Gated on the same permission as suspension -- the ability to
    take a tenant offline and the ability to put it back are the same
    operational authority.
    """

    def __init__(self, uow_factory: PlatformUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: ReactivateTenantCommand) -> None:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id) as uow:
            state = await compute_effective_platform_state(uow, actor_id, now=now)
            if _SUSPEND_PERMISSION not in state.permissions:
                raise TenantCreationDeniedError(_SUSPEND_PERMISSION)

            tenant = await uow.tenants.get_by_id(tenant_id)
            if tenant is None:
                raise TenantNotFoundError(str(tenant_id))

            tenant.activate(now=now)
            await uow.tenants.save(tenant)
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=None,
                tenant_id=tenant.id,
                action="tenancy.tenant_reactivated",
                resource_type="tenant",
                resource_id=tenant.id,
                result="success",
                metadata={},
            )


@dataclass(frozen=True, slots=True)
class RenameTenantCommand:
    actor_user_id: str
    tenant_id: str
    display_name: str


class RenameTenant:
    """Changes a tenant's display name. Gated on the same permission as
    creating one -- `platform.tenants.view` doesn't exist as a separate
    permission (see `list_tenants.py`'s docstring), and editing identity is
    at least as sensitive as creating it.

    Slug is intentionally not editable through this or any other use case --
    see `Tenant.rename()`'s docstring.
    """

    def __init__(self, uow_factory: PlatformUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: RenameTenantCommand) -> None:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id) as uow:
            state = await compute_effective_platform_state(uow, actor_id, now=now)
            if _REQUIRED_PERMISSION not in state.permissions:
                raise TenantCreationDeniedError(_REQUIRED_PERMISSION)

            tenant = await uow.tenants.get_by_id(tenant_id)
            if tenant is None:
                raise TenantNotFoundError(str(tenant_id))

            previous = tenant.display_name
            tenant.rename(display_name=command.display_name, now=now)
            await uow.tenants.save(tenant)
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=None,
                tenant_id=tenant.id,
                action="tenancy.tenant_renamed",
                resource_type="tenant",
                resource_id=tenant.id,
                result="success",
                metadata={"from": previous, "to": command.display_name},
            )
