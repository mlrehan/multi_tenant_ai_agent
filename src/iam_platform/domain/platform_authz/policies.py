"""Effective-permission resolution for platform scope -- docs/06-authorization-model.md.

No feature/plan entitlement filter here (that concept is tenant-scoped only,
per docs/13-schema-tenant-management.md); platform permissions are either
granted via a role or not.
"""

from __future__ import annotations

from uuid import UUID

from iam_platform.domain.shared.policies import expand_role_hierarchy
from iam_platform.domain.tenant_authz.entities import OverrideEffect


def resolve_effective_platform_permissions(
    *,
    assigned_role_ids: set[UUID],
    hierarchy_edges_by_parent: dict[UUID, list[UUID]],
    role_permission_codes_by_role: dict[UUID, set[str]],
    override_effect_by_permission_code: dict[str, OverrideEffect],
) -> frozenset[str]:
    """``override_effect_by_permission_code`` is pre-resolved by the caller
    (the application layer, which joins each active ``AuthorizationOverride``
    row to its permission's ``code`` via the catalog) -- kept out of this
    pure function so it doesn't need to know how overrides reference
    permissions internally.
    """
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

    return frozenset((allow | extra_allow) - deny_codes)
