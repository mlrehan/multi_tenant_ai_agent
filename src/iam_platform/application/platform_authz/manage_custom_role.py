"""Platform custom-role creation and permission assignment.

Mirrors `application/tenant_authz/manage_custom_role.py` almost exactly --
same self-escalation guard, same "system roles are immutable" rule, same
split between creation-time permission set and later add/remove. The
platform catalog is small and mostly bootstrap-seeded (see
`scripts/bootstrap_platform_admin.py`'s docstring: "an admin role should be
an intentional, reviewed set"), but nothing about the domain model actually
requires that every platform role be system-defined -- an operator may
legitimately want a narrower role than `platform_super_admin` for a support
lead or a billing contact, and the guard that keeps tenant custom roles safe
(nobody can define a role with more power than they hold) applies identically
here.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from iam_platform.application.platform_authz.effective_permissions import (
    compute_effective_platform_state,
)
from iam_platform.application.platform_authz.exceptions import (
    DuplicatePlatformRoleCodeError,
    PlatformPermissionNotFoundError,
    RoleNotFoundError,
    SelfEscalationError,
    SystemPlatformRoleImmutableError,
    UserManagementDeniedError,
)
from iam_platform.application.platform_authz.ports import PlatformUowFactory
from iam_platform.core.clock import Clock
from iam_platform.domain.platform_authz.entities import PlatformRole
from iam_platform.domain.shared.policies import can_assign_role

# Reuses the grant/revoke permission -- defining a role and granting one are
# the same authority (both put permissions into someone's hands, mediated by
# the self-escalation guard either way), and the catalog has no dedicated
# `platform.roles.manage` permission of its own.
_MANAGE_ROLES_PERMISSION = "platform.tenants.create"


@dataclass(frozen=True, slots=True)
class CreateCustomPlatformRoleCommand:
    actor_user_id: str
    code: str
    name: str
    description: str | None
    rank: int
    permission_codes: list[str]


class CreateCustomPlatformRole:
    def __init__(self, uow_factory: PlatformUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: CreateCustomPlatformRoleCommand) -> UUID:
        actor_id = UUID(command.actor_user_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id) as uow:
            actor_state = await compute_effective_platform_state(uow, actor_id, now=now)
            if _MANAGE_ROLES_PERMISSION not in actor_state.permissions:
                raise UserManagementDeniedError(_MANAGE_ROLES_PERMISSION)

            if await uow.platform_roles.get_by_code(command.code) is not None:
                raise DuplicatePlatformRoleCodeError(command.code)

            requested_codes = set(command.permission_codes)
            for code in requested_codes:
                if await uow.platform_permissions.get_by_code(code) is None:
                    raise PlatformPermissionNotFoundError(code)

            # Same reasoning as the tenant version: check the guard against
            # the role's own (not-yet-persisted) rank, so it can never be
            # *defined* with more power than its creator holds.
            violations = can_assign_role(
                actor_effective_permissions=actor_state.permissions,
                actor_highest_rank=actor_state.highest_role_rank,
                is_self_assignment=False,
                target_role_rank=command.rank,
                target_role_permission_codes=frozenset(requested_codes),
            )
            if violations:
                raise SelfEscalationError(violations)

            role = PlatformRole(
                id=uuid4(),
                code=command.code,
                name=command.name,
                description=command.description,
                is_system=False,
                rank=command.rank,
                created_at=now,
                updated_at=now,
            )
            await uow.platform_roles.add(role)
            for code in requested_codes:
                await uow.platform_permissions.assign_to_role(role_id=role.id, permission_code=code)

            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=None,
                tenant_id=None,
                action="platform_authz.custom_role_created",
                resource_type="platform_role",
                resource_id=role.id,
                result="success",
                metadata={"code": command.code, "permission_codes": sorted(requested_codes)},
            )
            return role.id


@dataclass(frozen=True, slots=True)
class PlatformRolePermissionCommand:
    actor_user_id: str
    role_code: str
    permission_code: str


class AddPermissionToPlatformRole:
    def __init__(self, uow_factory: PlatformUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: PlatformRolePermissionCommand) -> None:
        actor_id = UUID(command.actor_user_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id) as uow:
            actor_state = await compute_effective_platform_state(uow, actor_id, now=now)
            if _MANAGE_ROLES_PERMISSION not in actor_state.permissions:
                raise UserManagementDeniedError(_MANAGE_ROLES_PERMISSION)

            role = await uow.platform_roles.get_by_code(command.role_code)
            if role is None:
                raise RoleNotFoundError(command.role_code)
            if role.is_system:
                raise SystemPlatformRoleImmutableError(command.role_code)

            if await uow.platform_permissions.get_by_code(command.permission_code) is None:
                raise PlatformPermissionNotFoundError(command.permission_code)

            existing_codes = (
                await uow.platform_permissions.get_role_permission_codes({role.id})
            ).get(role.id, set())
            resulting_codes = existing_codes | {command.permission_code}

            violations = can_assign_role(
                actor_effective_permissions=actor_state.permissions,
                actor_highest_rank=actor_state.highest_role_rank,
                is_self_assignment=False,
                target_role_rank=role.rank,
                target_role_permission_codes=frozenset(resulting_codes),
            )
            if violations:
                raise SelfEscalationError(violations)

            await uow.platform_permissions.assign_to_role(
                role_id=role.id, permission_code=command.permission_code
            )
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=None,
                tenant_id=None,
                action="platform_authz.role_permission_added",
                resource_type="platform_role",
                resource_id=role.id,
                result="success",
                metadata={"permission_code": command.permission_code},
            )


class RemovePermissionFromPlatformRole:
    def __init__(self, uow_factory: PlatformUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: PlatformRolePermissionCommand) -> None:
        actor_id = UUID(command.actor_user_id)

        async with self._uow_factory(actor_id) as uow:
            actor_state = await compute_effective_platform_state(
                uow, actor_id, now=self._clock.now()
            )
            if _MANAGE_ROLES_PERMISSION not in actor_state.permissions:
                raise UserManagementDeniedError(_MANAGE_ROLES_PERMISSION)

            role = await uow.platform_roles.get_by_code(command.role_code)
            if role is None:
                raise RoleNotFoundError(command.role_code)
            if role.is_system:
                raise SystemPlatformRoleImmutableError(command.role_code)

            await uow.platform_permissions.revoke_from_role(
                role_id=role.id, permission_code=command.permission_code
            )
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=None,
                tenant_id=None,
                action="platform_authz.role_permission_removed",
                resource_type="platform_role",
                resource_id=role.id,
                result="success",
                metadata={"permission_code": command.permission_code},
            )
