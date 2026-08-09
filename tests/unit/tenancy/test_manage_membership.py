"""SuspendMembership/ReactivateMembership/RevokeMembership -- permission-gated
membership lifecycle transitions, independent flat use cases by design."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from iam_platform.application.tenancy.exceptions import MembershipNotFoundError, PermissionDeniedError
from iam_platform.application.tenancy.manage_membership import (
    MembershipLifecycleCommand,
    ReactivateMembership,
    RevokeMembership,
    SuspendMembership,
)
from iam_platform.core.clock import FixedClock
from iam_platform.domain.tenancy.entities import MembershipStatus, TenantMembership
from iam_platform.domain.tenant_authz.entities import TenantMembershipRole
from tests.unit.tenant_authz.fakes import FakeTenantUnitOfWork, make_tenant_role

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _seed_actor_with_manage_permission(uow: FakeTenantUnitOfWork, tenant_id):
    role = make_tenant_role(tenant_id=None, code="manager", rank=100, now=NOW, is_system=True)
    uow.tenant_roles.by_id[role.id] = role
    uow.tenant_permissions.role_permission_codes[role.id] = {"tenant.users.manage"}
    actor_id = uuid4()
    membership = TenantMembership(
        id=uuid4(),
        tenant_id=tenant_id,
        user_id=actor_id,
        status=MembershipStatus.ACTIVE,
        created_at=NOW,
        updated_at=NOW,
    )
    uow.tenant_memberships.by_id[membership.id] = membership
    uow.tenant_membership_roles.by_id[uuid4()] = TenantMembershipRole(
        id=uuid4(),
        tenant_id=tenant_id,
        membership_id=membership.id,
        role_id=role.id,
        granted_by_user_id=actor_id,
        granted_at=NOW,
    )
    return actor_id


class TestSuspendMembership:
    async def test_suspends_and_audits(self) -> None:
        uow = FakeTenantUnitOfWork()
        tenant_id = uuid4()
        actor_id = _seed_actor_with_manage_permission(uow, tenant_id)
        target = TenantMembership(
            id=uuid4(),
            tenant_id=tenant_id,
            user_id=uuid4(),
            status=MembershipStatus.ACTIVE,
            created_at=NOW,
            updated_at=NOW,
        )
        uow.tenant_memberships.by_id[target.id] = target

        use_case = SuspendMembership(uow, FixedClock(NOW))
        await use_case.execute(
            MembershipLifecycleCommand(
                actor_user_id=str(actor_id),
                tenant_id=str(tenant_id),
                target_membership_id=str(target.id),
                reason="policy violation",
            )
        )
        assert target.status == MembershipStatus.SUSPENDED
        assert uow.audit.events[0]["action"] == "tenancy.membership_suspended"

    async def test_denied_without_manage_permission(self) -> None:
        uow = FakeTenantUnitOfWork()
        tenant_id = uuid4()
        target = TenantMembership(
            id=uuid4(),
            tenant_id=tenant_id,
            user_id=uuid4(),
            status=MembershipStatus.ACTIVE,
            created_at=NOW,
            updated_at=NOW,
        )
        uow.tenant_memberships.by_id[target.id] = target

        use_case = SuspendMembership(uow, FixedClock(NOW))
        with pytest.raises(PermissionDeniedError):
            await use_case.execute(
                MembershipLifecycleCommand(
                    actor_user_id=str(uuid4()),
                    tenant_id=str(tenant_id),
                    target_membership_id=str(target.id),
                )
            )
        assert target.status == MembershipStatus.ACTIVE

    async def test_membership_in_another_tenant_not_found(self) -> None:
        uow = FakeTenantUnitOfWork()
        tenant_id = uuid4()
        other_tenant_id = uuid4()
        actor_id = _seed_actor_with_manage_permission(uow, tenant_id)
        foreign = TenantMembership(
            id=uuid4(),
            tenant_id=other_tenant_id,
            user_id=uuid4(),
            status=MembershipStatus.ACTIVE,
            created_at=NOW,
            updated_at=NOW,
        )
        uow.tenant_memberships.by_id[foreign.id] = foreign

        use_case = SuspendMembership(uow, FixedClock(NOW))
        with pytest.raises(MembershipNotFoundError):
            await use_case.execute(
                MembershipLifecycleCommand(
                    actor_user_id=str(actor_id),
                    tenant_id=str(tenant_id),
                    target_membership_id=str(foreign.id),
                )
            )


class TestReactivateMembership:
    async def test_reactivates_a_suspended_membership(self) -> None:
        uow = FakeTenantUnitOfWork()
        tenant_id = uuid4()
        actor_id = _seed_actor_with_manage_permission(uow, tenant_id)
        target = TenantMembership(
            id=uuid4(),
            tenant_id=tenant_id,
            user_id=uuid4(),
            status=MembershipStatus.SUSPENDED,
            suspended_reason="prior",
            created_at=NOW,
            updated_at=NOW,
        )
        uow.tenant_memberships.by_id[target.id] = target

        use_case = ReactivateMembership(uow, FixedClock(NOW))
        await use_case.execute(
            MembershipLifecycleCommand(
                actor_user_id=str(actor_id),
                tenant_id=str(tenant_id),
                target_membership_id=str(target.id),
            )
        )
        assert target.status == MembershipStatus.ACTIVE


class TestRevokeMembership:
    async def test_revokes_and_audits(self) -> None:
        uow = FakeTenantUnitOfWork()
        tenant_id = uuid4()
        actor_id = _seed_actor_with_manage_permission(uow, tenant_id)
        target = TenantMembership(
            id=uuid4(),
            tenant_id=tenant_id,
            user_id=uuid4(),
            status=MembershipStatus.ACTIVE,
            created_at=NOW,
            updated_at=NOW,
        )
        uow.tenant_memberships.by_id[target.id] = target

        use_case = RevokeMembership(uow, FixedClock(NOW))
        await use_case.execute(
            MembershipLifecycleCommand(
                actor_user_id=str(actor_id),
                tenant_id=str(tenant_id),
                target_membership_id=str(target.id),
                reason="offboarded",
            )
        )
        assert target.status == MembershipStatus.REVOKED
        assert uow.audit.events[0]["action"] == "tenancy.membership_revoked"
