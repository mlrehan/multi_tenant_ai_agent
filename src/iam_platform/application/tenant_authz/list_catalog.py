"""Tenant role/permission catalog listings -- non-sensitive reference data
(role and permission *codes and descriptions*, not grants) needed by
role-picker and permission-picker UIs (invite-with-roles, custom role
creation, override creation). Available to any active member of the tenant;
enumerating what a role or permission *is* doesn't require holding
`tenant.roles.manage`, only *assigning* one does.

No explicit membership check here: the `X-Tenant-Id` resolver dependency
(`api/deps/tenant_resolver.py`) already re-validates an active membership
before any route handler runs, so a route reaching this use case has already
proven that. Mirrors `ResolveTenantEffectivePermissions`'s own convention of
returning an empty result rather than raising when the (defensive,
should-be-unreachable) no-membership case occurs, instead of inventing a
permission-shaped error for something that isn't a permission check.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from iam_platform.application.tenant_authz.effective_permissions import compute_effective_tenant_state
from iam_platform.application.tenant_authz.ports import TenantUowFactory
from iam_platform.core.clock import Clock
from iam_platform.domain.tenant_authz.entities import TenantPermission, TenantRole


@dataclass(frozen=True, slots=True)
class TenantCatalogQuery:
    actor_user_id: str
    tenant_id: str


class ListTenantRoles:
    def __init__(self, uow_factory: TenantUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, query: TenantCatalogQuery) -> list[TenantRole]:
        actor_id = UUID(query.actor_user_id)
        tenant_id = UUID(query.tenant_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id, tenant_id) as uow:
            actor_state = await compute_effective_tenant_state(uow, tenant_id, actor_id, now=now)
            if actor_state is None:
                return []

            return await uow.tenant_roles.list_available_to_tenant(tenant_id)


class ListTenantPermissions:
    def __init__(self, uow_factory: TenantUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, query: TenantCatalogQuery) -> list[TenantPermission]:
        actor_id = UUID(query.actor_user_id)
        tenant_id = UUID(query.tenant_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id, tenant_id) as uow:
            actor_state = await compute_effective_tenant_state(uow, tenant_id, actor_id, now=now)
            if actor_state is None:
                return []

            return await uow.tenant_permissions.list_all()


@dataclass(frozen=True, slots=True)
class TenantRolePermissionMap:
    by_role_code: dict[str, list[str]]


class ListTenantRolePermissions:
    """Role -> permission-code mapping for the roles this tenant can use.

    Note this reports what each role *definition* grants, which is not the
    same as what a member holding it ends up with: role-hierarchy inheritance
    and explicit allow/deny overrides both apply on top, and only
    `ResolveTenantEffectivePermissions` accounts for those. The RBAC screen
    labels it accordingly rather than presenting it as the final answer.
    """

    def __init__(self, uow_factory: TenantUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, query: TenantCatalogQuery) -> TenantRolePermissionMap:
        actor_id = UUID(query.actor_user_id)
        tenant_id = UUID(query.tenant_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id, tenant_id) as uow:
            actor_state = await compute_effective_tenant_state(uow, tenant_id, actor_id, now=now)
            if actor_state is None:
                return TenantRolePermissionMap(by_role_code={})

            roles = await uow.tenant_roles.list_available_to_tenant(tenant_id)
            codes_by_id = await uow.tenant_permissions.get_role_permission_codes(
                {r.id for r in roles}
            )
            return TenantRolePermissionMap(
                by_role_code={
                    role.code: sorted(codes_by_id.get(role.id, set())) for role in roles
                }
            )
