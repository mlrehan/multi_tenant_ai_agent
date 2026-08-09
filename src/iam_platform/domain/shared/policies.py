"""RBAC algorithms shared by the platform and tenant authorization scopes --
docs/06-authorization-model.md. Scope-agnostic: both `platform_authz` and
`tenant_authz` operate on their own role-ID namespace but the hierarchy
expansion, cycle prevention, and self-escalation guard are identical in
shape, so they live here rather than being duplicated per scope.
"""

from __future__ import annotations

from uuid import UUID

from iam_platform.domain.shared.exceptions import InvariantViolationError

MAX_HIERARCHY_DEPTH = 8


def expand_role_hierarchy(
    role_ids: set[UUID],
    edges_by_parent: dict[UUID, list[UUID]],
    *,
    max_depth: int = MAX_HIERARCHY_DEPTH,
) -> set[UUID]:
    """Returns ``role_ids`` plus every role reachable by following
    parent -> child edges (a senior/parent role inherits all permissions of
    its junior/child roles).

    Fails *safe*, not loud: a cycle or an over-depth chain simply stops being
    expanded further (via the per-branch ``visited`` set) rather than raising
    -- permission resolution runs on every authorization check and must never
    crash due to bad hierarchy data, especially since a cycle can only exist
    here if it slipped past `validate_hierarchy_edge` (e.g. bad data
    inserted directly). It also can never expand into more access than a
    non-cyclic version of the same data would grant, so failing safe here
    doesn't create an escalation path.
    """
    expanded: set[UUID] = set()
    for role_id in role_ids:
        expanded |= _expand_one(role_id, edges_by_parent, visited={role_id}, depth=0, max_depth=max_depth)
    return expanded | role_ids


def _expand_one(
    role_id: UUID,
    edges_by_parent: dict[UUID, list[UUID]],
    *,
    visited: set[UUID],
    depth: int,
    max_depth: int,
) -> set[UUID]:
    if depth >= max_depth:
        return set()
    result: set[UUID] = set()
    for child_id in edges_by_parent.get(role_id, []):
        if child_id in visited:
            continue  # cycle guard
        result.add(child_id)
        result |= _expand_one(
            child_id, edges_by_parent, visited=visited | {child_id}, depth=depth + 1, max_depth=max_depth
        )
    return result


def validate_hierarchy_edge(
    parent_role_id: UUID,
    child_role_id: UUID,
    edges_by_parent: dict[UUID, list[UUID]],
    *,
    max_depth: int = MAX_HIERARCHY_DEPTH,
) -> None:
    """Raises if adding ``parent_role_id -> child_role_id`` would create a
    self-loop or a cycle. Called at WRITE time (before the edge is
    persisted) -- unlike ``expand_role_hierarchy``, failing loudly here is
    correct and desired, since this is the one place a cycle can be
    prevented from ever entering the data in the first place.
    """
    if parent_role_id == child_role_id:
        raise InvariantViolationError("a role cannot inherit from itself")

    reachable_from_child = _expand_one(
        child_role_id, edges_by_parent, visited={child_role_id}, depth=0, max_depth=max_depth
    )
    if parent_role_id in reachable_from_child:
        raise InvariantViolationError(
            "adding this edge would create a role-hierarchy cycle"
        )


def can_assign_role(
    *,
    actor_effective_permissions: frozenset[str],
    actor_highest_rank: int,
    is_self_assignment: bool,
    target_role_rank: int,
    target_role_permission_codes: frozenset[str],
) -> list[str]:
    """Self-escalation guard (docs/06-authorization-model.md): an actor may
    only grant a role whose permissions are a subset of their own effective
    permissions, and may never elevate their own access to a role of equal
    or higher rank than their current highest role.

    Returns a list of human-readable violation reasons; empty means allowed.
    """
    violations: list[str] = []

    if is_self_assignment and target_role_rank >= actor_highest_rank:
        violations.append("cannot elevate your own access to a role of equal or higher rank")

    missing = target_role_permission_codes - actor_effective_permissions
    if missing:
        violations.append(
            "cannot grant permissions you do not hold: " + ", ".join(sorted(missing))
        )

    return violations
