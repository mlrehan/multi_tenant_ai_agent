"""Tenant effective-permission resolution -- docs/06-authorization-model.md.

No Redis caching yet (Phase 6 scope note, CLAUDE.md) -- always computed fresh.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from iam_platform.application.tenant_authz.ports import TenantUnitOfWork, TenantUowFactory
from iam_platform.core.clock import Clock
from iam_platform.domain.impersonation.policies import (
    PermissionRisk,
    restrict_permissions_for_impersonation,
)
from iam_platform.domain.tenant_authz.entities import OverrideEffect, OverrideScope, RoleScope
from iam_platform.domain.tenant_authz.policies import (
    PermissionEntitlement,
    resolve_effective_tenant_permissions,
)


@dataclass(frozen=True, slots=True)
class ActorEffectiveTenantState:
    membership_id: UUID
    permissions: frozenset[str]
    highest_role_rank: int
    role_ids: frozenset[UUID]


async def compute_effective_tenant_state(
    uow: TenantUnitOfWork, tenant_id: UUID, user_id: UUID, *, now: datetime
) -> ActorEffectiveTenantState | None:
    membership = await uow.tenant_memberships.get_by_tenant_and_user(tenant_id, user_id)
    if membership is None or not membership.is_active:
        return None

    assignments = [
        a for a in await uow.tenant_membership_roles.list_active_by_membership(membership.id) if a.is_active
    ]
    role_ids = {a.role_id for a in assignments}

    if not role_ids:
        return ActorEffectiveTenantState(
            membership_id=membership.id, permissions=frozenset(), highest_role_rank=0, role_ids=frozenset()
        )

    roles = [r for r in [await uow.tenant_roles.get_by_id(rid) for rid in role_ids] if r is not None]
    highest_rank = max((r.rank for r in roles), default=0)

    edges = await uow.role_hierarchy.list_edges_by_parent(scope=RoleScope.TENANT, tenant_id=tenant_id)
    role_permission_codes = await uow.tenant_permissions.get_role_permission_codes(role_ids)

    overrides = await uow.authorization_overrides.list_active_for_subject(
        scope=OverrideScope.TENANT, tenant_id=tenant_id, subject_id=membership.id, now=now
    )
    override_effect_by_code: dict[str, OverrideEffect] = {}
    permission_catalog: dict[str, PermissionEntitlement] = {}
    for override in overrides:
        if override.tenant_permission_id is None:
            continue
        permission = await uow.tenant_permissions.get_by_id(override.tenant_permission_id)
        if permission is not None:
            override_effect_by_code[permission.code] = override.effect

    all_codes = {c for codes in role_permission_codes.values() for c in codes} | set(
        override_effect_by_code.keys()
    )
    if all_codes:
        for permission in await uow.tenant_permissions.list_by_codes(all_codes):
            permission_catalog[permission.code] = PermissionEntitlement(
                code=permission.code, required_feature=permission.required_feature
            )

    enabled_features = await uow.tenant_features.list_enabled_codes(tenant_id)

    permissions = resolve_effective_tenant_permissions(
        assigned_role_ids=role_ids,
        hierarchy_edges_by_parent=edges,
        role_permission_codes_by_role=role_permission_codes,
        override_effect_by_permission_code=override_effect_by_code,
        permission_catalog=permission_catalog,
        enabled_feature_codes=enabled_features,
    )
    return ActorEffectiveTenantState(
        membership_id=membership.id,
        permissions=permissions,
        highest_role_rank=highest_rank,
        role_ids=frozenset(role_ids),
    )


@dataclass(frozen=True, slots=True)
class ResolveTenantEffectivePermissionsQuery:
    tenant_id: str
    user_id: str
    #: Set when the request carries an ``act`` claim -- i.e. a platform support
    #: user is acting as ``user_id``. Narrows the result to the impersonation-
    #: safe subset (docs/03-threat-model.md scenario 9). Defaults to False so
    #: an ordinary session is never accidentally restricted.
    is_impersonated: bool = False


class ResolveTenantEffectivePermissions:
    def __init__(self, uow_factory: TenantUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, query: ResolveTenantEffectivePermissionsQuery) -> frozenset[str]:
        tenant_id = UUID(query.tenant_id)
        user_id = UUID(query.user_id)
        now = self._clock.now()
        async with self._uow_factory(user_id, tenant_id) as uow:
            state = await compute_effective_tenant_state(uow, tenant_id, user_id, now=now)
            if state is None:
                return frozenset()
            if not query.is_impersonated:
                return state.permissions

            # Load risk levels for exactly the codes in play, then narrow.
            catalog = await uow.tenant_permissions.list_by_codes(set(state.permissions))
            risk_by_code = {
                p.code: PermissionRisk(code=p.code, risk_level=p.risk_level) for p in catalog
            }
            return restrict_permissions_for_impersonation(
                target_permissions=state.permissions, risk_by_code=risk_by_code
            )
