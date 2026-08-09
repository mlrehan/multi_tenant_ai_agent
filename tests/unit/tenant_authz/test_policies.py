from datetime import UTC, datetime
from uuid import uuid4

import pytest

from iam_platform.domain.shared.exceptions import InvariantViolationError
from iam_platform.domain.shared.policies import (
    can_assign_role,
    expand_role_hierarchy,
    validate_hierarchy_edge,
)
from iam_platform.domain.tenant_authz.entities import OverrideEffect
from iam_platform.domain.tenant_authz.policies import (
    PermissionEntitlement,
    resolve_effective_tenant_permissions,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


class TestExpandRoleHierarchy:
    def test_no_edges_returns_only_assigned_roles(self) -> None:
        role = uuid4()
        assert expand_role_hierarchy({role}, {}) == {role}

    def test_follows_parent_to_child_chain(self) -> None:
        admin, manager, member = uuid4(), uuid4(), uuid4()
        edges = {admin: [manager], manager: [member]}
        result = expand_role_hierarchy({admin}, edges)
        assert result == {admin, manager, member}

    def test_does_not_expand_upward(self) -> None:
        admin, member = uuid4(), uuid4()
        edges = {admin: [member]}
        # Holding only the junior role must not pull in the senior role's permissions.
        assert expand_role_hierarchy({member}, edges) == {member}

    def test_cycle_does_not_infinite_loop_and_stays_bounded(self) -> None:
        a, b, c = uuid4(), uuid4(), uuid4()
        edges = {a: [b], b: [c], c: [a]}  # a -> b -> c -> a
        result = expand_role_hierarchy({a}, edges)
        assert result == {a, b, c}  # bounded, not an infinite set

    def test_depth_beyond_max_is_not_expanded(self) -> None:
        roles = [uuid4() for _ in range(10)]
        edges = {roles[i]: [roles[i + 1]] for i in range(9)}
        result = expand_role_hierarchy({roles[0]}, edges, max_depth=3)
        # Only 3 levels deep from roles[0]: roles[1], roles[2], roles[3]
        assert roles[4] not in result
        assert roles[3] in result


class TestValidateHierarchyEdge:
    def test_self_loop_rejected(self) -> None:
        role = uuid4()
        with pytest.raises(InvariantViolationError):
            validate_hierarchy_edge(role, role, {})

    def test_valid_edge_accepted(self) -> None:
        parent, child = uuid4(), uuid4()
        validate_hierarchy_edge(parent, child, {})  # must not raise

    def test_edge_that_would_close_a_cycle_rejected(self) -> None:
        a, b = uuid4(), uuid4()
        existing_edges = {a: [b]}  # a -> b already exists
        # Adding b -> a would close the loop.
        with pytest.raises(InvariantViolationError):
            validate_hierarchy_edge(b, a, existing_edges)

    def test_edge_that_would_close_a_longer_cycle_rejected(self) -> None:
        a, b, c = uuid4(), uuid4(), uuid4()
        existing_edges = {a: [b], b: [c]}  # a -> b -> c
        with pytest.raises(InvariantViolationError):
            validate_hierarchy_edge(c, a, existing_edges)  # c -> a would close a-b-c-a


class TestResolveEffectiveTenantPermissions:
    def test_union_of_role_permissions(self) -> None:
        role_a, role_b = uuid4(), uuid4()
        result = resolve_effective_tenant_permissions(
            assigned_role_ids={role_a, role_b},
            hierarchy_edges_by_parent={},
            role_permission_codes_by_role={
                role_a: {"tenant.assistants.view"},
                role_b: {"tenant.assistants.publish"},
            },
            override_effect_by_permission_code={},
            permission_catalog={},
            enabled_feature_codes=set(),
        )
        assert result == frozenset({"tenant.assistants.view", "tenant.assistants.publish"})

    def test_explicit_deny_overrides_a_role_grant(self) -> None:
        role = uuid4()
        result = resolve_effective_tenant_permissions(
            assigned_role_ids={role},
            hierarchy_edges_by_parent={},
            role_permission_codes_by_role={role: {"tenant.assistants.publish"}},
            override_effect_by_permission_code={"tenant.assistants.publish": OverrideEffect.DENY},
            permission_catalog={},
            enabled_feature_codes=set(),
        )
        assert result == frozenset()

    def test_explicit_allow_grants_a_permission_no_role_holds(self) -> None:
        result = resolve_effective_tenant_permissions(
            assigned_role_ids=set(),
            hierarchy_edges_by_parent={},
            role_permission_codes_by_role={},
            override_effect_by_permission_code={"tenant.audit.view": OverrideEffect.ALLOW},
            permission_catalog={},
            enabled_feature_codes=set(),
        )
        assert result == frozenset({"tenant.audit.view"})

    def test_permission_requiring_unavailable_feature_is_filtered_out(self) -> None:
        role = uuid4()
        result = resolve_effective_tenant_permissions(
            assigned_role_ids={role},
            hierarchy_edges_by_parent={},
            role_permission_codes_by_role={role: {"tenant.assistants.publish"}},
            override_effect_by_permission_code={},
            permission_catalog={
                "tenant.assistants.publish": PermissionEntitlement(
                    code="tenant.assistants.publish", required_feature="premium_assistants"
                )
            },
            enabled_feature_codes=set(),  # tenant does NOT have premium_assistants
        )
        assert result == frozenset()

    def test_permission_requiring_available_feature_is_kept(self) -> None:
        role = uuid4()
        result = resolve_effective_tenant_permissions(
            assigned_role_ids={role},
            hierarchy_edges_by_parent={},
            role_permission_codes_by_role={role: {"tenant.assistants.publish"}},
            override_effect_by_permission_code={},
            permission_catalog={
                "tenant.assistants.publish": PermissionEntitlement(
                    code="tenant.assistants.publish", required_feature="premium_assistants"
                )
            },
            enabled_feature_codes={"premium_assistants"},
        )
        assert result == frozenset({"tenant.assistants.publish"})

    def test_hierarchy_expansion_feeds_into_permission_union(self) -> None:
        admin, member = uuid4(), uuid4()
        result = resolve_effective_tenant_permissions(
            assigned_role_ids={admin},
            hierarchy_edges_by_parent={admin: [member]},
            role_permission_codes_by_role={
                admin: {"tenant.roles.manage"},
                member: {"tenant.conversations.view"},
            },
            override_effect_by_permission_code={},
            permission_catalog={},
            enabled_feature_codes=set(),
        )
        assert result == frozenset({"tenant.roles.manage", "tenant.conversations.view"})


class TestCanAssignRole:
    def test_allowed_when_actor_holds_all_target_permissions_and_not_self_elevating(self) -> None:
        violations = can_assign_role(
            actor_effective_permissions=frozenset({"tenant.roles.manage", "tenant.users.invite"}),
            actor_highest_rank=10,
            is_self_assignment=False,
            target_role_rank=5,
            target_role_permission_codes=frozenset({"tenant.users.invite"}),
        )
        assert violations == []

    def test_rejects_granting_a_permission_the_actor_lacks(self) -> None:
        violations = can_assign_role(
            actor_effective_permissions=frozenset({"tenant.users.invite"}),
            actor_highest_rank=10,
            is_self_assignment=False,
            target_role_rank=5,
            target_role_permission_codes=frozenset({"tenant.billing.manage"}),
        )
        assert len(violations) == 1
        assert "tenant.billing.manage" in violations[0]

    def test_rejects_self_elevation_to_equal_or_higher_rank(self) -> None:
        violations = can_assign_role(
            actor_effective_permissions=frozenset({"tenant.roles.manage"}),
            actor_highest_rank=5,
            is_self_assignment=True,
            target_role_rank=5,
            target_role_permission_codes=frozenset(),
        )
        assert any("elevate" in v for v in violations)

    def test_allows_self_assignment_to_a_strictly_lower_rank(self) -> None:
        violations = can_assign_role(
            actor_effective_permissions=frozenset({"tenant.roles.manage"}),
            actor_highest_rank=10,
            is_self_assignment=True,
            target_role_rank=5,
            target_role_permission_codes=frozenset(),
        )
        assert violations == []
