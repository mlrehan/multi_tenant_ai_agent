"""CreateRoleHierarchyEdge -- cycle prevention and the self-escalation guard
applied to a parent role's *inherited* permissions, not just its own."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from iam_platform.application.tenant_authz.exceptions import PermissionDeniedError, SelfEscalationError
from iam_platform.application.tenant_authz.manage_role_hierarchy import (
    CreateRoleHierarchyEdge,
    CreateRoleHierarchyEdgeCommand,
)
from iam_platform.core.clock import FixedClock
from iam_platform.domain.tenancy.entities import MembershipStatus, TenantMembership
from iam_platform.domain.tenant_authz.entities import RoleHierarchyEdge, RoleScope, TenantMembershipRole
from tests.unit.tenant_authz.fakes import FakeTenantUnitOfWork, make_tenant_role

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _seed_actor(uow: FakeTenantUnitOfWork, tenant_id, permission_codes: set[str], rank: int):
    actor_role = make_tenant_role(tenant_id=None, code="actor_role", rank=rank, now=NOW, is_system=True)
    uow.tenant_roles.by_id[actor_role.id] = actor_role
    uow.tenant_permissions.role_permission_codes[actor_role.id] = permission_codes
    actor_user_id = uuid4()
    membership = TenantMembership(
        id=uuid4(),
        tenant_id=tenant_id,
        user_id=actor_user_id,
        status=MembershipStatus.ACTIVE,
        created_at=NOW,
        updated_at=NOW,
    )
    uow.tenant_memberships.by_id[membership.id] = membership
    uow.tenant_membership_roles.by_id[uuid4()] = TenantMembershipRole(
        id=uuid4(),
        tenant_id=tenant_id,
        membership_id=membership.id,
        role_id=actor_role.id,
        granted_by_user_id=actor_user_id,
        granted_at=NOW,
    )
    return actor_user_id


class TestCreateRoleHierarchyEdge:
    async def test_creates_edge_when_child_permissions_are_a_subset_of_actors(self) -> None:
        uow = FakeTenantUnitOfWork()
        tenant_id = uuid4()
        actor_user_id = _seed_actor(
            uow, tenant_id, {"tenant.roles.manage", "tenant.resources.read"}, rank=100
        )
        parent = make_tenant_role(tenant_id=tenant_id, code="parent", rank=50, now=NOW)
        child = make_tenant_role(tenant_id=tenant_id, code="child", rank=10, now=NOW)
        uow.tenant_roles.by_id[parent.id] = parent
        uow.tenant_roles.by_id[child.id] = child
        uow.tenant_permissions.role_permission_codes[child.id] = {"tenant.resources.read"}

        use_case = CreateRoleHierarchyEdge(uow, FixedClock(NOW))
        await use_case.execute(
            CreateRoleHierarchyEdgeCommand(
                actor_user_id=str(actor_user_id),
                tenant_id=str(tenant_id),
                parent_role_code="parent",
                child_role_code="child",
            )
        )

        assert len(uow.role_hierarchy.edges) == 1
        assert uow.audit.events[0]["action"] == "tenant_authz.role_hierarchy_edge_created"

    async def test_cannot_link_a_child_whose_permissions_actor_lacks(self) -> None:
        uow = FakeTenantUnitOfWork()
        tenant_id = uuid4()
        actor_user_id = _seed_actor(uow, tenant_id, {"tenant.roles.manage"}, rank=100)
        parent = make_tenant_role(tenant_id=tenant_id, code="parent", rank=50, now=NOW)
        child = make_tenant_role(tenant_id=tenant_id, code="child", rank=10, now=NOW)
        uow.tenant_roles.by_id[parent.id] = parent
        uow.tenant_roles.by_id[child.id] = child
        uow.tenant_permissions.role_permission_codes[child.id] = {"tenant.billing.manage"}

        use_case = CreateRoleHierarchyEdge(uow, FixedClock(NOW))
        with pytest.raises(SelfEscalationError):
            await use_case.execute(
                CreateRoleHierarchyEdgeCommand(
                    actor_user_id=str(actor_user_id),
                    tenant_id=str(tenant_id),
                    parent_role_code="parent",
                    child_role_code="child",
                )
            )
        assert uow.role_hierarchy.edges == []

    async def test_self_loop_rejected(self) -> None:
        uow = FakeTenantUnitOfWork()
        tenant_id = uuid4()
        actor_user_id = _seed_actor(uow, tenant_id, {"tenant.roles.manage"}, rank=100)
        role = make_tenant_role(tenant_id=tenant_id, code="solo", rank=50, now=NOW)
        uow.tenant_roles.by_id[role.id] = role

        use_case = CreateRoleHierarchyEdge(uow, FixedClock(NOW))
        with pytest.raises(SelfEscalationError):
            await use_case.execute(
                CreateRoleHierarchyEdgeCommand(
                    actor_user_id=str(actor_user_id),
                    tenant_id=str(tenant_id),
                    parent_role_code="solo",
                    child_role_code="solo",
                )
            )

    async def test_cycle_rejected(self) -> None:
        uow = FakeTenantUnitOfWork()
        tenant_id = uuid4()
        actor_user_id = _seed_actor(uow, tenant_id, {"tenant.roles.manage"}, rank=100)
        role_a = make_tenant_role(tenant_id=tenant_id, code="a", rank=50, now=NOW)
        role_b = make_tenant_role(tenant_id=tenant_id, code="b", rank=40, now=NOW)
        uow.tenant_roles.by_id[role_a.id] = role_a
        uow.tenant_roles.by_id[role_b.id] = role_b
        # existing edge: a -> b
        uow.role_hierarchy.edges.append(
            RoleHierarchyEdge(
                id=uuid4(),
                parent_role_id=role_a.id,
                child_role_id=role_b.id,
                role_scope=RoleScope.TENANT,
                tenant_id=tenant_id,
                created_at=NOW,
            )
        )

        use_case = CreateRoleHierarchyEdge(uow, FixedClock(NOW))
        with pytest.raises(SelfEscalationError):  # wraps InvariantViolationError
            await use_case.execute(
                CreateRoleHierarchyEdgeCommand(
                    actor_user_id=str(actor_user_id),
                    tenant_id=str(tenant_id),
                    parent_role_code="b",
                    child_role_code="a",
                )
            )

    async def test_denied_without_manage_roles_permission(self) -> None:
        uow = FakeTenantUnitOfWork()
        tenant_id = uuid4()
        use_case = CreateRoleHierarchyEdge(uow, FixedClock(NOW))
        with pytest.raises(PermissionDeniedError):
            await use_case.execute(
                CreateRoleHierarchyEdgeCommand(
                    actor_user_id=str(uuid4()),
                    tenant_id=str(tenant_id),
                    parent_role_code="a",
                    child_role_code="b",
                )
            )
