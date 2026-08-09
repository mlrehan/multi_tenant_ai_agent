"""List every membership in a tenant, and a single membership's active
role assignments -- the read side of member management, backing the
admin-panel roster view.

Deliberately returns the `identity`-owned `user_id` only, not an email --
`TenantUnitOfWork` has no access to the `identity` bounded context's
repositories, and joining across that boundary from tenant-authz
infrastructure would blur a separation this project has kept deliberately
strict since Phase 5 (docs/20-dependency-rules.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from iam_platform.application.tenancy.exceptions import MembershipNotFoundError, PermissionDeniedError
from iam_platform.application.tenant_authz.effective_permissions import compute_effective_tenant_state
from iam_platform.application.tenant_authz.ports import TenantUowFactory
from iam_platform.core.clock import Clock
from iam_platform.domain.tenancy.entities import TenantMembership
from iam_platform.domain.tenant_authz.entities import TenantMembershipRole

_MANAGE_MEMBERS_PERMISSION = "tenant.users.manage"


@dataclass(frozen=True, slots=True)
class ListTenantMembersQuery:
    actor_user_id: str
    tenant_id: str


class ListTenantMembers:
    def __init__(self, uow_factory: TenantUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, query: ListTenantMembersQuery) -> list[TenantMembership]:
        actor_id = UUID(query.actor_user_id)
        tenant_id = UUID(query.tenant_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id, tenant_id) as uow:
            actor_state = await compute_effective_tenant_state(uow, tenant_id, actor_id, now=now)
            if actor_state is None or _MANAGE_MEMBERS_PERMISSION not in actor_state.permissions:
                raise PermissionDeniedError(_MANAGE_MEMBERS_PERMISSION)

            return await uow.tenant_memberships.list_by_tenant(tenant_id)


@dataclass(frozen=True, slots=True)
class ListMembershipRolesQuery:
    actor_user_id: str
    tenant_id: str
    target_membership_id: str


class ListMembershipRoles:
    """Active role assignments for one membership -- used to expand a member
    row without paying for an N+1 join across the whole roster."""

    def __init__(self, uow_factory: TenantUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, query: ListMembershipRolesQuery) -> list[TenantMembershipRole]:
        actor_id = UUID(query.actor_user_id)
        tenant_id = UUID(query.tenant_id)
        target_id = UUID(query.target_membership_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id, tenant_id) as uow:
            actor_state = await compute_effective_tenant_state(uow, tenant_id, actor_id, now=now)
            if actor_state is None or _MANAGE_MEMBERS_PERMISSION not in actor_state.permissions:
                raise PermissionDeniedError(_MANAGE_MEMBERS_PERMISSION)

            membership = await uow.tenant_memberships.get_by_id(target_id)
            if membership is None or membership.tenant_id != tenant_id:
                raise MembershipNotFoundError(query.target_membership_id)

            return await uow.tenant_membership_roles.list_active_by_membership(target_id)
