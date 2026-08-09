"""Platform effective-permission resolution -- docs/06-authorization-model.md.

No Redis caching yet (Phase 6 scope note, CLAUDE.md) -- always computed fresh.
Correctness first; caching is a pure performance layer that can be added
later behind the same ``ResolvePlatformEffectivePermissions`` call shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from iam_platform.application.platform_authz.ports import PlatformUnitOfWork, PlatformUowFactory
from iam_platform.core.clock import Clock
from iam_platform.domain.platform_authz.policies import resolve_effective_platform_permissions
from iam_platform.domain.tenant_authz.entities import OverrideEffect, OverrideScope, RoleScope


@dataclass(frozen=True, slots=True)
class ActorEffectivePlatformState:
    permissions: frozenset[str]
    highest_role_rank: int
    role_ids: frozenset[UUID]


async def compute_effective_platform_state(
    uow: PlatformUnitOfWork, user_id: UUID, *, now: datetime
) -> ActorEffectivePlatformState:
    assignments = [a for a in await uow.platform_user_roles.list_active_by_user(user_id) if a.is_active]
    role_ids = {a.role_id for a in assignments}

    if not role_ids:
        return ActorEffectivePlatformState(permissions=frozenset(), highest_role_rank=0, role_ids=frozenset())

    roles = [r for r in [await uow.platform_roles.get_by_id(rid) for rid in role_ids] if r is not None]
    highest_rank = max((r.rank for r in roles), default=0)

    edges = await uow.role_hierarchy.list_edges_by_parent(scope=RoleScope.PLATFORM, tenant_id=None)
    role_permission_codes = await uow.platform_permissions.get_role_permission_codes(role_ids)

    overrides = await uow.authorization_overrides.list_active_for_subject(
        scope=OverrideScope.PLATFORM, tenant_id=None, subject_id=user_id, now=now
    )
    override_effect_by_code: dict[str, OverrideEffect] = {}
    for override in overrides:
        if override.platform_permission_id is None:
            continue
        permission = await uow.platform_permissions.get_by_id(override.platform_permission_id)
        if permission is not None:
            override_effect_by_code[permission.code] = override.effect

    permissions = resolve_effective_platform_permissions(
        assigned_role_ids=role_ids,
        hierarchy_edges_by_parent=edges,
        role_permission_codes_by_role=role_permission_codes,
        override_effect_by_permission_code=override_effect_by_code,
    )
    return ActorEffectivePlatformState(
        permissions=permissions, highest_role_rank=highest_rank, role_ids=frozenset(role_ids)
    )


@dataclass(frozen=True, slots=True)
class ResolvePlatformEffectivePermissionsQuery:
    user_id: str


class ResolvePlatformEffectivePermissions:
    def __init__(self, uow_factory: PlatformUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, query: ResolvePlatformEffectivePermissionsQuery) -> frozenset[str]:
        user_id = UUID(query.user_id)
        now = self._clock.now()
        async with self._uow_factory(user_id) as uow:
            state = await compute_effective_platform_state(uow, user_id, now=now)
            return state.permissions
