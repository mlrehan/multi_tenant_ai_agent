"""Effective-permission resolution for tenant scope -- docs/06-authorization-model.md.

Mirrors ``domain.platform_authz.policies`` with one addition: a feature
entitlement filter (``required_feature``), since plan/feature gating is a
tenant-scoped concept. ``required_plan`` filtering is deferred -- see the
Phase 6 scope note (CLAUDE.md); no ``tenant_subscriptions`` table exists yet,
so there is nothing to filter against.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from iam_platform.domain.shared.policies import expand_role_hierarchy
from iam_platform.domain.tenant_authz.entities import OverrideEffect


@dataclass(frozen=True, slots=True)
class PermissionEntitlement:
    code: str
    required_feature: str | None


def resolve_effective_tenant_permissions(
    *,
    assigned_role_ids: set[UUID],
    hierarchy_edges_by_parent: dict[UUID, list[UUID]],
    role_permission_codes_by_role: dict[UUID, set[str]],
    override_effect_by_permission_code: dict[str, OverrideEffect],
    permission_catalog: dict[str, PermissionEntitlement],
    enabled_feature_codes: set[str],
) -> frozenset[str]:
    expanded_roles = expand_role_hierarchy(assigned_role_ids, hierarchy_edges_by_parent)

    allow: set[str] = set()
    for role_id in expanded_roles:
        allow |= role_permission_codes_by_role.get(role_id, set())

    deny_codes = {
        code
        for code, effect in override_effect_by_permission_code.items()
        if effect == OverrideEffect.DENY
    }
    extra_allow = {
        code
        for code, effect in override_effect_by_permission_code.items()
        if effect == OverrideEffect.ALLOW
    }
    allow = (allow | extra_allow) - deny_codes

    def entitled(code: str) -> bool:
        entitlement = permission_catalog.get(code)
        if entitlement is None or entitlement.required_feature is None:
            return True
        return entitlement.required_feature in enabled_feature_codes

    return frozenset(code for code in allow if entitled(code))
