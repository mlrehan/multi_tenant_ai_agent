"""CreateAuthorizationOverride/RevokeAuthorizationOverride -- an ALLOW
override is gated by the self-escalation guard exactly like granting a
single-permission role; a DENY override skips that check since it can only
remove access, never grant it."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from iam_platform.application.tenant_authz.exceptions import (
    MembershipNotFoundError,
    PermissionDeniedError,
    PermissionNotFoundError,
    SelfEscalationError,
)
from iam_platform.application.tenant_authz.manage_override import (
    CreateAuthorizationOverride,
    CreateAuthorizationOverrideCommand,
    RevokeAuthorizationOverride,
    RevokeAuthorizationOverrideCommand,
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


def _seed_actor_and_target(uow: FakeTenantUnitOfWork, tenant_id, permission_codes: set[str]):
    role = make_tenant_role(tenant_id=None, code="actor_role", rank=100, now=NOW, is_system=True)
    uow.tenant_roles.by_id[role.id] = role
    uow.tenant_permissions.role_permission_codes[role.id] = permission_codes
    actor_user_id = uuid4()
    actor_membership = TenantMembership(
        id=uuid4(),
        tenant_id=tenant_id,
        user_id=actor_user_id,
        status=MembershipStatus.ACTIVE,
        created_at=NOW,
        updated_at=NOW,
    )
    uow.tenant_memberships.by_id[actor_membership.id] = actor_membership
    uow.tenant_membership_roles.by_id[uuid4()] = TenantMembershipRole(
        id=uuid4(),
        tenant_id=tenant_id,
        membership_id=actor_membership.id,
        role_id=role.id,
        granted_by_user_id=actor_user_id,
        granted_at=NOW,
    )
    target_membership = TenantMembership(
        id=uuid4(),
        tenant_id=tenant_id,
        user_id=uuid4(),
        status=MembershipStatus.ACTIVE,
        created_at=NOW,
        updated_at=NOW,
    )
    uow.tenant_memberships.by_id[target_membership.id] = target_membership
    return actor_user_id, target_membership


class TestCreateAuthorizationOverride:
    async def test_allow_override_requires_actor_to_hold_the_permission(self) -> None:
        uow = FakeTenantUnitOfWork()
        tenant_id = uuid4()
        actor_user_id, target_membership = _seed_actor_and_target(
            uow, tenant_id, {"tenant.roles.manage", "tenant.billing.read"}
        )
        perm = make_tenant_permission(code="tenant.billing.read", now=NOW)
        uow.tenant_permissions.by_id[perm.id] = perm

        use_case = CreateAuthorizationOverride(uow, FixedClock(NOW))
        override_id = await use_case.execute(
            CreateAuthorizationOverrideCommand(
                actor_user_id=str(actor_user_id),
                tenant_id=str(tenant_id),
                target_membership_id=str(target_membership.id),
                permission_code="tenant.billing.read",
                effect="allow",
                reason="temp access for audit",
            )
        )
        assert override_id in uow.authorization_overrides.by_id
        assert uow.audit.events[0]["action"] == "tenant_authz.override_created"

    async def test_allow_override_rejected_if_actor_lacks_the_permission(self) -> None:
        uow = FakeTenantUnitOfWork()
        tenant_id = uuid4()
        actor_user_id, target_membership = _seed_actor_and_target(uow, tenant_id, {"tenant.roles.manage"})
        perm = make_tenant_permission(code="tenant.billing.read", now=NOW)
        uow.tenant_permissions.by_id[perm.id] = perm

        use_case = CreateAuthorizationOverride(uow, FixedClock(NOW))
        with pytest.raises(SelfEscalationError):
            await use_case.execute(
                CreateAuthorizationOverrideCommand(
                    actor_user_id=str(actor_user_id),
                    tenant_id=str(tenant_id),
                    target_membership_id=str(target_membership.id),
                    permission_code="tenant.billing.read",
                    effect="allow",
                    reason="trying to grant what I don't have",
                )
            )
        assert uow.authorization_overrides.by_id == {}

    async def test_deny_override_does_not_require_holding_the_permission(self) -> None:
        uow = FakeTenantUnitOfWork()
        tenant_id = uuid4()
        actor_user_id, target_membership = _seed_actor_and_target(uow, tenant_id, {"tenant.roles.manage"})
        perm = make_tenant_permission(code="tenant.billing.read", now=NOW)
        uow.tenant_permissions.by_id[perm.id] = perm

        use_case = CreateAuthorizationOverride(uow, FixedClock(NOW))
        override_id = await use_case.execute(
            CreateAuthorizationOverrideCommand(
                actor_user_id=str(actor_user_id),
                tenant_id=str(tenant_id),
                target_membership_id=str(target_membership.id),
                permission_code="tenant.billing.read",
                effect="deny",
                reason="lock this member out",
            )
        )
        assert override_id in uow.authorization_overrides.by_id

    async def test_unknown_permission_code_rejected(self) -> None:
        uow = FakeTenantUnitOfWork()
        tenant_id = uuid4()
        actor_user_id, target_membership = _seed_actor_and_target(uow, tenant_id, {"tenant.roles.manage"})

        use_case = CreateAuthorizationOverride(uow, FixedClock(NOW))
        with pytest.raises(PermissionNotFoundError):
            await use_case.execute(
                CreateAuthorizationOverrideCommand(
                    actor_user_id=str(actor_user_id),
                    tenant_id=str(tenant_id),
                    target_membership_id=str(target_membership.id),
                    permission_code="does.not.exist",
                    effect="deny",
                    reason="x",
                )
            )

    async def test_target_membership_in_another_tenant_rejected(self) -> None:
        uow = FakeTenantUnitOfWork()
        tenant_id = uuid4()
        other_tenant_id = uuid4()
        actor_user_id, _ = _seed_actor_and_target(uow, tenant_id, {"tenant.roles.manage"})
        perm = make_tenant_permission(code="tenant.billing.read", now=NOW)
        uow.tenant_permissions.by_id[perm.id] = perm
        foreign_membership = TenantMembership(
            id=uuid4(),
            tenant_id=other_tenant_id,
            user_id=uuid4(),
            status=MembershipStatus.ACTIVE,
            created_at=NOW,
            updated_at=NOW,
        )
        uow.tenant_memberships.by_id[foreign_membership.id] = foreign_membership

        use_case = CreateAuthorizationOverride(uow, FixedClock(NOW))
        with pytest.raises(MembershipNotFoundError):
            await use_case.execute(
                CreateAuthorizationOverrideCommand(
                    actor_user_id=str(actor_user_id),
                    tenant_id=str(tenant_id),
                    target_membership_id=str(foreign_membership.id),
                    permission_code="tenant.billing.read",
                    effect="deny",
                    reason="x",
                )
            )


class TestRevokeAuthorizationOverride:
    async def test_denied_without_manage_roles_permission(self) -> None:
        uow = FakeTenantUnitOfWork()
        tenant_id = uuid4()
        use_case = RevokeAuthorizationOverride(uow, FixedClock(NOW))
        with pytest.raises(PermissionDeniedError):
            await use_case.execute(
                RevokeAuthorizationOverrideCommand(
                    actor_user_id=str(uuid4()), tenant_id=str(tenant_id), override_id=str(uuid4())
                )
            )
