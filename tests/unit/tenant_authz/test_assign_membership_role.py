"""AssignMembershipRole/RevokeMembershipRole -- self-escalation guard at the
tenant scope, mirroring the platform-scope proof in
``tests/unit/platform_authz/test_grant_platform_role.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from iam_platform.application.tenant_authz.assign_membership_role import (
    AssignMembershipRole,
    AssignMembershipRoleCommand,
    RevokeMembershipRole,
    RevokeMembershipRoleCommand,
)
from iam_platform.application.tenant_authz.exceptions import PermissionDeniedError, SelfEscalationError
from iam_platform.core.clock import FixedClock
from iam_platform.domain.tenancy.entities import MembershipStatus, TenantMembership
from iam_platform.domain.tenant_authz.entities import TenantMembershipRole
from tests.unit.tenant_authz.fakes import FakeTenantUnitOfWork, make_tenant_role

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _seed_tenant_with_admin_and_member_roles(uow: FakeTenantUnitOfWork, tenant_id):
    admin_role = make_tenant_role(tenant_id=None, code="admin", rank=100, now=NOW, is_system=True)
    member_role = make_tenant_role(tenant_id=None, code="member", rank=10, now=NOW, is_system=True)
    uow.tenant_roles.by_id[admin_role.id] = admin_role
    uow.tenant_roles.by_id[member_role.id] = member_role
    uow.tenant_permissions.role_permission_codes[admin_role.id] = {
        "tenant.roles.manage",
        "tenant.users.manage",
        "tenant.resources.read",
    }
    uow.tenant_permissions.role_permission_codes[member_role.id] = {"tenant.resources.read"}
    return admin_role, member_role


def _active_membership(tenant_id, user_id) -> TenantMembership:
    return TenantMembership(
        id=uuid4(),
        tenant_id=tenant_id,
        user_id=user_id,
        status=MembershipStatus.ACTIVE,
        created_at=NOW,
        updated_at=NOW,
    )


class TestAssignMembershipRole:
    async def test_admin_can_assign_a_lesser_role_to_another_member(self) -> None:
        uow = FakeTenantUnitOfWork()
        tenant_id = uuid4()
        admin_role, member_role = _seed_tenant_with_admin_and_member_roles(uow, tenant_id)

        actor_user_id, target_user_id = uuid4(), uuid4()
        actor_membership = _active_membership(tenant_id, actor_user_id)
        target_membership = _active_membership(tenant_id, target_user_id)
        uow.tenant_memberships.by_id[actor_membership.id] = actor_membership
        uow.tenant_memberships.by_id[target_membership.id] = target_membership
        uow.tenant_membership_roles.by_id[uuid4()] = TenantMembershipRole(
            id=uuid4(),
            tenant_id=tenant_id,
            membership_id=actor_membership.id,
            role_id=admin_role.id,
            granted_by_user_id=actor_user_id,
            granted_at=NOW,
        )

        use_case = AssignMembershipRole(uow, FixedClock(NOW))
        await use_case.execute(
            AssignMembershipRoleCommand(
                actor_user_id=str(actor_user_id),
                tenant_id=str(tenant_id),
                target_membership_id=str(target_membership.id),
                role_code="member",
            )
        )

        assignment = await uow.tenant_membership_roles.get_active(
            membership_id=target_membership.id, role_id=member_role.id
        )
        assert assignment is not None
        assert uow.audit.events[0]["action"] == "tenant_authz.role_assigned"

    async def test_actor_cannot_self_assign_a_role_of_equal_or_higher_rank(self) -> None:
        uow = FakeTenantUnitOfWork()
        tenant_id = uuid4()
        admin_role, member_role = _seed_tenant_with_admin_and_member_roles(uow, tenant_id)
        # Grant the actor every permission "admin" carries via a same-permission
        # lower-rank role, isolating the rank check from the permission check.
        mid_role = make_tenant_role(tenant_id=None, code="mid", rank=50, now=NOW, is_system=True)
        uow.tenant_roles.by_id[mid_role.id] = mid_role
        uow.tenant_permissions.role_permission_codes[mid_role.id] = {
            "tenant.roles.manage",
            "tenant.users.manage",
        }

        actor_user_id = uuid4()
        actor_membership = _active_membership(tenant_id, actor_user_id)
        uow.tenant_memberships.by_id[actor_membership.id] = actor_membership
        uow.tenant_membership_roles.by_id[uuid4()] = TenantMembershipRole(
            id=uuid4(),
            tenant_id=tenant_id,
            membership_id=actor_membership.id,
            role_id=mid_role.id,
            granted_by_user_id=actor_user_id,
            granted_at=NOW,
        )

        use_case = AssignMembershipRole(uow, FixedClock(NOW))
        with pytest.raises(SelfEscalationError):
            await use_case.execute(
                AssignMembershipRoleCommand(
                    actor_user_id=str(actor_user_id),
                    tenant_id=str(tenant_id),
                    target_membership_id=str(actor_membership.id),
                    role_code="admin",
                )
            )

    async def test_denied_without_manage_roles_permission(self) -> None:
        uow = FakeTenantUnitOfWork()
        tenant_id = uuid4()
        _, member_role = _seed_tenant_with_admin_and_member_roles(uow, tenant_id)
        actor_user_id, target_user_id = uuid4(), uuid4()
        actor_membership = _active_membership(tenant_id, actor_user_id)
        target_membership = _active_membership(tenant_id, target_user_id)
        uow.tenant_memberships.by_id[actor_membership.id] = actor_membership
        uow.tenant_memberships.by_id[target_membership.id] = target_membership
        # actor has no role assignments at all -> no permissions

        use_case = AssignMembershipRole(uow, FixedClock(NOW))
        with pytest.raises(PermissionDeniedError):
            await use_case.execute(
                AssignMembershipRoleCommand(
                    actor_user_id=str(actor_user_id),
                    tenant_id=str(tenant_id),
                    target_membership_id=str(target_membership.id),
                    role_code="member",
                )
            )


class TestRevokeMembershipRole:
    async def test_revoke_is_idempotent_and_audits_once(self) -> None:
        uow = FakeTenantUnitOfWork()
        tenant_id = uuid4()
        admin_role, member_role = _seed_tenant_with_admin_and_member_roles(uow, tenant_id)
        actor_user_id, target_user_id = uuid4(), uuid4()
        actor_membership = _active_membership(tenant_id, actor_user_id)
        target_membership = _active_membership(tenant_id, target_user_id)
        uow.tenant_memberships.by_id[actor_membership.id] = actor_membership
        uow.tenant_memberships.by_id[target_membership.id] = target_membership
        uow.tenant_membership_roles.by_id[uuid4()] = TenantMembershipRole(
            id=uuid4(),
            tenant_id=tenant_id,
            membership_id=actor_membership.id,
            role_id=admin_role.id,
            granted_by_user_id=actor_user_id,
            granted_at=NOW,
        )
        assignment = TenantMembershipRole(
            id=uuid4(),
            tenant_id=tenant_id,
            membership_id=target_membership.id,
            role_id=member_role.id,
            granted_by_user_id=actor_user_id,
            granted_at=NOW,
        )
        uow.tenant_membership_roles.by_id[assignment.id] = assignment

        use_case = RevokeMembershipRole(uow, FixedClock(NOW))
        command = RevokeMembershipRoleCommand(
            actor_user_id=str(actor_user_id),
            tenant_id=str(tenant_id),
            target_membership_id=str(target_membership.id),
            role_code="member",
        )
        await use_case.execute(command)
        await use_case.execute(command)

        assert assignment.revoked_at == NOW
        assert len(uow.audit.events) == 1
