"""GrantPlatformRole/RevokePlatformRole -- self-escalation guard proof
(docs/06-authorization-model.md): an actor can only grant a role whose
permissions are a subset of their own, and can never elevate their own rank.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from iam_platform.application.platform_authz.exceptions import RoleNotFoundError, SelfEscalationError
from iam_platform.application.platform_authz.grant_platform_role import (
    GrantPlatformRole,
    GrantPlatformRoleCommand,
    RevokePlatformRole,
    RevokePlatformRoleCommand,
)
from iam_platform.core.clock import FixedClock
from iam_platform.domain.platform_authz.entities import PlatformUserRole
from tests.unit.tenant_authz.fakes import FakePlatformUnitOfWork, make_platform_role

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _seed_admin_and_support_roles(uow: FakePlatformUnitOfWork) -> tuple[object, object]:
    admin_role = make_platform_role(code="admin", rank=100, now=NOW)
    support_role = make_platform_role(code="support", rank=10, now=NOW)
    uow.platform_roles.by_id[admin_role.id] = admin_role
    uow.platform_roles.by_id[support_role.id] = support_role
    uow.platform_permissions.role_permission_codes[admin_role.id] = {
        "platform.users.manage",
        "platform.tenants.create",
        "platform.support.impersonate",
    }
    uow.platform_permissions.role_permission_codes[support_role.id] = {"platform.support.impersonate"}
    return admin_role, support_role


class TestGrantPlatformRole:
    async def test_actor_with_superset_permissions_can_grant_a_lesser_role(self) -> None:
        uow = FakePlatformUnitOfWork()
        admin_role, support_role = _seed_admin_and_support_roles(uow)
        actor_id, target_id = uuid4(), uuid4()
        uow.platform_user_roles.by_id[uuid4()] = PlatformUserRole(
            id=uuid4(), user_id=actor_id, role_id=admin_role.id, granted_by_user_id=actor_id, granted_at=NOW
        )

        use_case = GrantPlatformRole(uow, FixedClock(NOW))
        await use_case.execute(
            GrantPlatformRoleCommand(
                actor_user_id=str(actor_id), target_user_id=str(target_id), role_code="support"
            )
        )

        granted = await uow.platform_user_roles.get_active(user_id=target_id, role_id=support_role.id)
        assert granted is not None
        assert len(uow.audit.events) == 1
        assert uow.audit.events[0]["action"] == "platform_authz.role_granted"

    async def test_actor_cannot_grant_permissions_they_do_not_hold(self) -> None:
        uow = FakePlatformUnitOfWork()
        _, support_role = _seed_admin_and_support_roles(uow)
        # actor only holds "support" (a subset), tries to grant "support" to
        # someone else while lacking one of its own held permissions is not
        # the test -- instead: actor holds NO roles at all, tries to grant
        # "support" which carries a permission they don't have.
        actor_id, target_id = uuid4(), uuid4()

        use_case = GrantPlatformRole(uow, FixedClock(NOW))
        with pytest.raises(SelfEscalationError):
            await use_case.execute(
                GrantPlatformRoleCommand(
                    actor_user_id=str(actor_id), target_user_id=str(target_id), role_code="support"
                )
            )

        assert await uow.platform_user_roles.get_active(user_id=target_id, role_id=support_role.id) is None
        assert uow.audit.events == []  # no side effect survives the raise

    async def test_actor_cannot_elevate_their_own_rank_via_self_assignment(self) -> None:
        uow = FakePlatformUnitOfWork()
        admin_role, support_role = _seed_admin_and_support_roles(uow)
        # give the actor every permission "admin" carries, at "support" rank,
        # so the permission-subset check passes but the rank check must still
        # block a self-grant of the higher-ranked role.
        actor_id = uuid4()
        low_rank_role = make_platform_role(code="low", rank=5, now=NOW)
        uow.platform_roles.by_id[low_rank_role.id] = low_rank_role
        uow.platform_permissions.role_permission_codes[low_rank_role.id] = {
            "platform.users.manage",
            "platform.tenants.create",
        }
        uow.platform_user_roles.by_id[uuid4()] = PlatformUserRole(
            id=uuid4(),
            user_id=actor_id,
            role_id=low_rank_role.id,
            granted_by_user_id=actor_id,
            granted_at=NOW,
        )

        use_case = GrantPlatformRole(uow, FixedClock(NOW))
        with pytest.raises(SelfEscalationError):
            await use_case.execute(
                GrantPlatformRoleCommand(
                    actor_user_id=str(actor_id), target_user_id=str(actor_id), role_code="admin"
                )
            )

    async def test_unknown_role_code_raises(self) -> None:
        uow = FakePlatformUnitOfWork()
        use_case = GrantPlatformRole(uow, FixedClock(NOW))
        with pytest.raises(RoleNotFoundError):
            await use_case.execute(
                GrantPlatformRoleCommand(
                    actor_user_id=str(uuid4()), target_user_id=str(uuid4()), role_code="nonexistent"
                )
            )

    async def test_grant_is_idempotent(self) -> None:
        uow = FakePlatformUnitOfWork()
        admin_role, support_role = _seed_admin_and_support_roles(uow)
        actor_id, target_id = uuid4(), uuid4()
        uow.platform_user_roles.by_id[uuid4()] = PlatformUserRole(
            id=uuid4(), user_id=actor_id, role_id=admin_role.id, granted_by_user_id=actor_id, granted_at=NOW
        )
        use_case = GrantPlatformRole(uow, FixedClock(NOW))
        command = GrantPlatformRoleCommand(
            actor_user_id=str(actor_id), target_user_id=str(target_id), role_code="support"
        )
        await use_case.execute(command)
        await use_case.execute(command)  # second call must not raise or duplicate

        assignments = [a for a in uow.platform_user_roles.by_id.values() if a.user_id == target_id]
        assert len(assignments) == 1
        assert len(uow.audit.events) == 1  # only the first grant recorded


class TestRevokePlatformRole:
    """Revoke is gated by the same self-escalation guard as grant (fixed
    alongside the admin-panel API work -- previously `RevokePlatformRole`
    had no authorization check at all, so any bearer token could strip any
    platform user's role). Every test here that expects revoke to *succeed*
    must therefore give the actor a role whose permissions are a superset of
    the role being revoked, exactly like the grant tests above.
    """

    async def test_revoking_an_unheld_role_is_idempotent(self) -> None:
        uow = FakePlatformUnitOfWork()
        admin_role, _ = _seed_admin_and_support_roles(uow)
        actor_id = uuid4()
        uow.platform_user_roles.by_id[uuid4()] = PlatformUserRole(
            id=uuid4(), user_id=actor_id, role_id=admin_role.id, granted_by_user_id=actor_id, granted_at=NOW
        )

        use_case = RevokePlatformRole(uow, FixedClock(NOW))
        await use_case.execute(
            RevokePlatformRoleCommand(
                actor_user_id=str(actor_id), target_user_id=str(uuid4()), role_code="support"
            )
        )
        assert uow.audit.events == []

    async def test_revoke_marks_assignment_revoked_and_audits(self) -> None:
        uow = FakePlatformUnitOfWork()
        admin_role, support_role = _seed_admin_and_support_roles(uow)
        actor_id, target_id = uuid4(), uuid4()
        uow.platform_user_roles.by_id[uuid4()] = PlatformUserRole(
            id=uuid4(), user_id=actor_id, role_id=admin_role.id, granted_by_user_id=actor_id, granted_at=NOW
        )
        assignment = PlatformUserRole(
            id=uuid4(),
            user_id=target_id,
            role_id=support_role.id,
            granted_by_user_id=actor_id,
            granted_at=NOW,
        )
        uow.platform_user_roles.by_id[assignment.id] = assignment

        use_case = RevokePlatformRole(uow, FixedClock(NOW))
        await use_case.execute(
            RevokePlatformRoleCommand(
                actor_user_id=str(actor_id), target_user_id=str(target_id), role_code="support"
            )
        )

        assert assignment.revoked_at == NOW
        assert uow.audit.events[0]["action"] == "platform_authz.role_revoked"

    async def test_actor_without_the_roles_permissions_cannot_revoke_it_from_someone_else(
        self,
    ) -> None:
        """The fix: an under-privileged actor must not be able to strip a
        role from another user just by knowing their user id and the role
        code."""
        uow = FakePlatformUnitOfWork()
        _, support_role = _seed_admin_and_support_roles(uow)
        actor_id, target_id = uuid4(), uuid4()  # actor holds NO platform role at all
        assignment = PlatformUserRole(
            id=uuid4(),
            user_id=target_id,
            role_id=support_role.id,
            granted_by_user_id=target_id,
            granted_at=NOW,
        )
        uow.platform_user_roles.by_id[assignment.id] = assignment

        use_case = RevokePlatformRole(uow, FixedClock(NOW))
        with pytest.raises(SelfEscalationError):
            await use_case.execute(
                RevokePlatformRoleCommand(
                    actor_user_id=str(actor_id), target_user_id=str(target_id), role_code="support"
                )
            )

        # The target's role assignment must survive the denied attempt.
        assert assignment.revoked_at is None
        assert uow.audit.events == []
