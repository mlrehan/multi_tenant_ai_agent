"""CreateCustomPlatformRole / Add|RemovePermissionToPlatformRole -- mirrors
tests/unit/tenant_authz/test_manage_custom_role.py's coverage for the
platform-scope equivalent: the self-escalation guard applies identically,
and system roles are immutable identically.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from iam_platform.application.platform_authz.exceptions import (
    DuplicatePlatformRoleCodeError,
    PlatformPermissionNotFoundError,
    RoleNotFoundError,
    SelfEscalationError,
    SystemPlatformRoleImmutableError,
    UserManagementDeniedError,
)
from iam_platform.application.platform_authz.manage_custom_role import (
    AddPermissionToPlatformRole,
    CreateCustomPlatformRole,
    CreateCustomPlatformRoleCommand,
    PlatformRolePermissionCommand,
    RemovePermissionFromPlatformRole,
)
from iam_platform.core.clock import FixedClock
from iam_platform.domain.platform_authz.entities import PlatformPermission, PlatformRole, PlatformUserRole
from tests.unit.tenant_authz.fakes import FakePlatformUnitOfWork, make_platform_role

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _grant_actor(uow: FakePlatformUnitOfWork, actor_id, *, rank: int, codes: set[str]) -> PlatformRole:
    role = make_platform_role(code=f"actor-role-{uuid4().hex[:6]}", rank=rank, now=NOW)
    uow.platform_roles.by_id[role.id] = role
    uow.platform_permissions.role_permission_codes[role.id] = set(codes)
    uow.platform_user_roles.by_id[uuid4()] = PlatformUserRole(
        id=uuid4(), user_id=actor_id, role_id=role.id, granted_by_user_id=actor_id, granted_at=NOW
    )
    return role


def _seed_permission(uow: FakePlatformUnitOfWork, code: str) -> None:
    permission = PlatformPermission(
        id=uuid4(), code=code, resource=code.split(".")[0], action=code.split(".")[-1], created_at=NOW
    )
    uow.platform_permissions.by_id[permission.id] = permission


class TestCreateCustomPlatformRole:
    async def test_creates_role_with_permissions_the_actor_holds(self) -> None:
        uow = FakePlatformUnitOfWork()
        actor_id = uuid4()
        _grant_actor(uow, actor_id, rank=100, codes={"platform.tenants.create", "platform.users.read"})
        _seed_permission(uow, "platform.users.read")

        use_case = CreateCustomPlatformRole(uow, FixedClock(NOW))
        role_id = await use_case.execute(
            CreateCustomPlatformRoleCommand(
                actor_user_id=str(actor_id),
                code="support_reader",
                name="Support Reader",
                description=None,
                rank=10,
                permission_codes=["platform.users.read"],
            )
        )

        created = uow.platform_roles.by_id[role_id]
        assert created.is_system is False
        assert uow.platform_permissions.role_permission_codes[role_id] == {"platform.users.read"}

    async def test_rejects_without_the_gating_permission(self) -> None:
        uow = FakePlatformUnitOfWork()
        actor_id = uuid4()
        # Actor holds nothing at all -- lacks platform.tenants.create.

        use_case = CreateCustomPlatformRole(uow, FixedClock(NOW))
        with pytest.raises(UserManagementDeniedError):
            await use_case.execute(
                CreateCustomPlatformRoleCommand(
                    actor_user_id=str(actor_id),
                    code="anything",
                    name="Anything",
                    description=None,
                    rank=10,
                    permission_codes=[],
                )
            )

    async def test_rejects_a_duplicate_code(self) -> None:
        uow = FakePlatformUnitOfWork()
        actor_id = uuid4()
        _grant_actor(uow, actor_id, rank=100, codes={"platform.tenants.create"})
        existing = make_platform_role(code="dup", rank=5, now=NOW)
        uow.platform_roles.by_id[existing.id] = existing

        use_case = CreateCustomPlatformRole(uow, FixedClock(NOW))
        with pytest.raises(DuplicatePlatformRoleCodeError):
            await use_case.execute(
                CreateCustomPlatformRoleCommand(
                    actor_user_id=str(actor_id),
                    code="dup",
                    name="Dup",
                    description=None,
                    rank=10,
                    permission_codes=[],
                )
            )

    async def test_cannot_define_a_role_with_permissions_the_actor_lacks(self) -> None:
        """The self-escalation guard at definition time, not just assignment
        time -- an actor cannot write themselves a role containing power they
        don't already hold, even though nobody has assigned it to anyone yet."""
        uow = FakePlatformUnitOfWork()
        actor_id = uuid4()
        _grant_actor(uow, actor_id, rank=100, codes={"platform.tenants.create"})
        _seed_permission(uow, "platform.support.impersonate")

        use_case = CreateCustomPlatformRole(uow, FixedClock(NOW))
        with pytest.raises(SelfEscalationError):
            await use_case.execute(
                CreateCustomPlatformRoleCommand(
                    actor_user_id=str(actor_id),
                    code="over_reaching",
                    name="Over-reaching",
                    description=None,
                    rank=10,
                    permission_codes=["platform.support.impersonate"],
                )
            )

    async def test_rank_alone_does_not_gate_definition_only_self_assignment_does(self) -> None:
        """`can_assign_role`'s rank check only fires when `is_self_assignment`
        is true, and role *definition* always passes `is_self_assignment=False`
        (identical to the tenant version) -- so a role ranked above its
        creator is not itself a violation as long as it contains no
        permission the creator lacks. The rank check's job is to stop the
        creator from later *taking* that role for themselves, which is
        `grant_platform_role.py`'s concern, not this one's.
        """
        uow = FakePlatformUnitOfWork()
        actor_id = uuid4()
        _grant_actor(uow, actor_id, rank=50, codes={"platform.tenants.create"})

        use_case = CreateCustomPlatformRole(uow, FixedClock(NOW))
        role_id = await use_case.execute(
            CreateCustomPlatformRoleCommand(
                actor_user_id=str(actor_id),
                code="high_ranked_but_empty",
                name="High Ranked But Empty",
                description=None,
                rank=999,
                permission_codes=[],
            )
        )
        assert uow.platform_roles.by_id[role_id].rank == 999


class TestAddPermissionToPlatformRole:
    async def test_adds_a_permission_the_actor_holds(self) -> None:
        uow = FakePlatformUnitOfWork()
        actor_id = uuid4()
        _grant_actor(uow, actor_id, rank=100, codes={"platform.tenants.create", "platform.users.read"})
        _seed_permission(uow, "platform.users.read")
        role = make_platform_role(code="custom", rank=10, now=NOW)
        uow.platform_roles.by_id[role.id] = role

        use_case = AddPermissionToPlatformRole(uow, FixedClock(NOW))
        await use_case.execute(
            PlatformRolePermissionCommand(
                actor_user_id=str(actor_id), role_code="custom", permission_code="platform.users.read"
            )
        )

        assert "platform.users.read" in uow.platform_permissions.role_permission_codes[role.id]

    async def test_rejects_adding_a_permission_the_actor_lacks(self) -> None:
        uow = FakePlatformUnitOfWork()
        actor_id = uuid4()
        _grant_actor(uow, actor_id, rank=100, codes={"platform.tenants.create"})
        _seed_permission(uow, "platform.support.impersonate")
        role = make_platform_role(code="custom", rank=10, now=NOW)
        uow.platform_roles.by_id[role.id] = role

        use_case = AddPermissionToPlatformRole(uow, FixedClock(NOW))
        with pytest.raises(SelfEscalationError):
            await use_case.execute(
                PlatformRolePermissionCommand(
                    actor_user_id=str(actor_id),
                    role_code="custom",
                    permission_code="platform.support.impersonate",
                )
            )

    async def test_rejects_editing_a_system_role(self) -> None:
        uow = FakePlatformUnitOfWork()
        actor_id = uuid4()
        _grant_actor(uow, actor_id, rank=1000, codes={"platform.tenants.create"})
        _seed_permission(uow, "platform.users.read")
        system_role = PlatformRole(
            id=uuid4(), code="platform_super_admin", name="Super Admin", is_system=True, rank=1000,
            created_at=NOW, updated_at=NOW,
        )
        uow.platform_roles.by_id[system_role.id] = system_role

        use_case = AddPermissionToPlatformRole(uow, FixedClock(NOW))
        with pytest.raises(SystemPlatformRoleImmutableError):
            await use_case.execute(
                PlatformRolePermissionCommand(
                    actor_user_id=str(actor_id),
                    role_code="platform_super_admin",
                    permission_code="platform.users.read",
                )
            )

    async def test_unknown_role_is_404_not_500(self) -> None:
        uow = FakePlatformUnitOfWork()
        actor_id = uuid4()
        _grant_actor(uow, actor_id, rank=100, codes={"platform.tenants.create"})

        use_case = AddPermissionToPlatformRole(uow, FixedClock(NOW))
        with pytest.raises(RoleNotFoundError):
            await use_case.execute(
                PlatformRolePermissionCommand(
                    actor_user_id=str(actor_id), role_code="no-such-role", permission_code="x.y.z"
                )
            )

    async def test_unknown_permission_is_404_not_500(self) -> None:
        uow = FakePlatformUnitOfWork()
        actor_id = uuid4()
        _grant_actor(uow, actor_id, rank=100, codes={"platform.tenants.create"})
        role = make_platform_role(code="custom", rank=10, now=NOW)
        uow.platform_roles.by_id[role.id] = role

        use_case = AddPermissionToPlatformRole(uow, FixedClock(NOW))
        with pytest.raises(PlatformPermissionNotFoundError):
            await use_case.execute(
                PlatformRolePermissionCommand(
                    actor_user_id=str(actor_id), role_code="custom", permission_code="no.such.permission"
                )
            )


class TestRemovePermissionFromPlatformRole:
    async def test_removes_a_permission_no_escalation_check_needed(self) -> None:
        uow = FakePlatformUnitOfWork()
        actor_id = uuid4()
        # Actor holds only the gating permission -- removing power from a
        # role can never grant the actor anything, so no other grant is needed.
        _grant_actor(uow, actor_id, rank=10, codes={"platform.tenants.create"})
        role = make_platform_role(code="custom", rank=500, now=NOW)
        uow.platform_roles.by_id[role.id] = role
        uow.platform_permissions.role_permission_codes[role.id] = {"platform.support.impersonate"}

        use_case = RemovePermissionFromPlatformRole(uow, FixedClock(NOW))
        await use_case.execute(
            PlatformRolePermissionCommand(
                actor_user_id=str(actor_id),
                role_code="custom",
                permission_code="platform.support.impersonate",
            )
        )

        assert uow.platform_permissions.role_permission_codes[role.id] == set()

    async def test_rejects_editing_a_system_role(self) -> None:
        uow = FakePlatformUnitOfWork()
        actor_id = uuid4()
        _grant_actor(uow, actor_id, rank=1000, codes={"platform.tenants.create"})
        system_role = PlatformRole(
            id=uuid4(), code="platform_super_admin", name="Super Admin", is_system=True, rank=1000,
            created_at=NOW, updated_at=NOW,
        )
        uow.platform_roles.by_id[system_role.id] = system_role

        use_case = RemovePermissionFromPlatformRole(uow, FixedClock(NOW))
        with pytest.raises(SystemPlatformRoleImmutableError):
            await use_case.execute(
                PlatformRolePermissionCommand(
                    actor_user_id=str(actor_id),
                    role_code="platform_super_admin",
                    permission_code="platform.tenants.create",
                )
            )
