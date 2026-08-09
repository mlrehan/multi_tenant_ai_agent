"""CreateCustomRole -- a custom role can never be *defined* with more power
than its creator holds (self-escalation guard applied at definition time,
not just assignment time)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from iam_platform.application.tenant_authz.exceptions import (
    DuplicateRoleCodeError,
    PermissionDeniedError,
    PermissionNotFoundError,
    PermissionNotTenantCustomizableError,
    SelfEscalationError,
)
from iam_platform.application.tenant_authz.manage_custom_role import (
    CreateCustomRole,
    CreateCustomRoleCommand,
)
from iam_platform.core.clock import FixedClock
from iam_platform.domain.tenancy.entities import MembershipStatus, TenantMembership
from iam_platform.domain.tenant_authz.entities import TenantMembershipRole
from tests.unit.tenant_authz.fakes import (
    FakeTenantUnitOfWork,
    make_tenant_permission,
    make_tenant_role,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _seed_actor_with_role(uow: FakeTenantUnitOfWork, tenant_id, permission_codes: set[str], rank: int):
    role = make_tenant_role(tenant_id=None, code="creator", rank=rank, now=NOW, is_system=True)
    uow.tenant_roles.by_id[role.id] = role
    uow.tenant_permissions.role_permission_codes[role.id] = permission_codes
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
        role_id=role.id,
        granted_by_user_id=actor_user_id,
        granted_at=NOW,
    )
    return actor_user_id


class TestCreateCustomRole:
    async def test_creates_role_when_requested_permissions_are_a_subset(self) -> None:
        uow = FakeTenantUnitOfWork()
        tenant_id = uuid4()
        actor_user_id = _seed_actor_with_role(
            uow, tenant_id, {"tenant.roles.manage", "tenant.resources.read"}, rank=100
        )
        perm = make_tenant_permission(code="tenant.resources.read", now=NOW)
        uow.tenant_permissions.by_id[perm.id] = perm

        use_case = CreateCustomRole(uow, FixedClock(NOW))
        role_id = await use_case.execute(
            CreateCustomRoleCommand(
                actor_user_id=str(actor_user_id),
                tenant_id=str(tenant_id),
                code="reader",
                name="Reader",
                description=None,
                rank=5,
                permission_codes=["tenant.resources.read"],
            )
        )

        role = await uow.tenant_roles.get_by_id(role_id)
        assert role is not None
        assert role.is_system is False
        assert uow.audit.events[0]["action"] == "tenant_authz.custom_role_created"

    async def test_cannot_define_a_role_with_permissions_actor_lacks(self) -> None:
        uow = FakeTenantUnitOfWork()
        tenant_id = uuid4()
        actor_user_id = _seed_actor_with_role(uow, tenant_id, {"tenant.roles.manage"}, rank=100)
        perm = make_tenant_permission(code="tenant.billing.manage", now=NOW)
        uow.tenant_permissions.by_id[perm.id] = perm

        use_case = CreateCustomRole(uow, FixedClock(NOW))
        with pytest.raises(SelfEscalationError):
            await use_case.execute(
                CreateCustomRoleCommand(
                    actor_user_id=str(actor_user_id),
                    tenant_id=str(tenant_id),
                    code="finance",
                    name="Finance",
                    description=None,
                    rank=5,
                    permission_codes=["tenant.billing.manage"],
                )
            )

    async def test_permission_not_tenant_customizable_rejected(self) -> None:
        uow = FakeTenantUnitOfWork()
        tenant_id = uuid4()
        actor_user_id = _seed_actor_with_role(uow, tenant_id, {"tenant.roles.manage"}, rank=100)
        perm = make_tenant_permission(code="tenant.core.locked", now=NOW, tenant_customizable=False)
        uow.tenant_permissions.by_id[perm.id] = perm
        # not_customizable is checked before the self-escalation guard, so
        # this fails regardless of whether the actor holds the permission.

        use_case = CreateCustomRole(uow, FixedClock(NOW))
        with pytest.raises(PermissionNotTenantCustomizableError):
            await use_case.execute(
                CreateCustomRoleCommand(
                    actor_user_id=str(actor_user_id),
                    tenant_id=str(tenant_id),
                    code="locked_role",
                    name="Locked",
                    description=None,
                    rank=5,
                    permission_codes=["tenant.core.locked"],
                )
            )

    async def test_unknown_permission_code_rejected(self) -> None:
        uow = FakeTenantUnitOfWork()
        tenant_id = uuid4()
        actor_user_id = _seed_actor_with_role(uow, tenant_id, {"tenant.roles.manage"}, rank=100)

        use_case = CreateCustomRole(uow, FixedClock(NOW))
        with pytest.raises(PermissionNotFoundError):
            await use_case.execute(
                CreateCustomRoleCommand(
                    actor_user_id=str(actor_user_id),
                    tenant_id=str(tenant_id),
                    code="ghost",
                    name="Ghost",
                    description=None,
                    rank=5,
                    permission_codes=["does.not.exist"],
                )
            )

    async def test_duplicate_role_code_within_tenant_rejected(self) -> None:
        uow = FakeTenantUnitOfWork()
        tenant_id = uuid4()
        actor_user_id = _seed_actor_with_role(uow, tenant_id, {"tenant.roles.manage"}, rank=100)
        existing = make_tenant_role(tenant_id=tenant_id, code="dup", rank=1, now=NOW)
        uow.tenant_roles.by_id[existing.id] = existing

        use_case = CreateCustomRole(uow, FixedClock(NOW))
        with pytest.raises(DuplicateRoleCodeError):
            await use_case.execute(
                CreateCustomRoleCommand(
                    actor_user_id=str(actor_user_id),
                    tenant_id=str(tenant_id),
                    code="dup",
                    name="Dup",
                    description=None,
                    rank=5,
                    permission_codes=[],
                )
            )

    async def test_denied_without_manage_roles_permission(self) -> None:
        uow = FakeTenantUnitOfWork()
        tenant_id = uuid4()
        actor_user_id = uuid4()  # no membership/role at all

        use_case = CreateCustomRole(uow, FixedClock(NOW))
        with pytest.raises(PermissionDeniedError):
            await use_case.execute(
                CreateCustomRoleCommand(
                    actor_user_id=str(actor_user_id),
                    tenant_id=str(tenant_id),
                    code="anything",
                    name="Anything",
                    description=None,
                    rank=5,
                    permission_codes=[],
                )
            )
