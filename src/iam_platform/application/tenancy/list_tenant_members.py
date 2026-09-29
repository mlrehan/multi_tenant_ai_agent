"""List every membership in a tenant, and a single membership's active
role assignments -- the read side of member management, backing the
admin-panel roster view.

`TenantUnitOfWork` still has no access to the `identity` bounded context's
repositories (docs/20-dependency-rules.md). What it has instead is
`member_directory`, a two-field projection -- email and display name, for
this tenant's own members only -- because a roster of UUIDs is a roster no
administrator can act on: they cannot tell whom they are about to suspend.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from iam_platform.application.tenancy.exceptions import MembershipNotFoundError, PermissionDeniedError
from iam_platform.application.tenancy.ports import MemberContact
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

    async def execute_with_contacts(
        self, query: ListTenantMembersQuery
    ) -> list[tuple[TenantMembership, MemberContact | None]]:
        """The roster with each member's email and name.

        Behind the *same* permission check as `execute`, inside the same unit
        of work: who may see the roster is exactly who may see whom it lists.
        """
        actor_id = UUID(query.actor_user_id)
        tenant_id = UUID(query.tenant_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id, tenant_id) as uow:
            actor_state = await compute_effective_tenant_state(uow, tenant_id, actor_id, now=now)
            if actor_state is None or _MANAGE_MEMBERS_PERMISSION not in actor_state.permissions:
                raise PermissionDeniedError(_MANAGE_MEMBERS_PERMISSION)

            members = await uow.tenant_memberships.list_by_tenant(tenant_id)
            contacts = await uow.member_directory.contacts(tenant_id)
            return [(m, contacts.get(m.id)) for m in members]


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
