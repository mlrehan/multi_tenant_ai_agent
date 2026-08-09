"""Tenant custom-role creation and permission assignment -- docs/06-authorization-model.md,
docs/14-schema-tenant-authorization.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from iam_platform.application.tenant_authz.effective_permissions import compute_effective_tenant_state
from iam_platform.application.tenant_authz.exceptions import (
    DuplicateRoleCodeError,
    PermissionDeniedError,
    PermissionNotFoundError,
    PermissionNotTenantCustomizableError,
    RoleNotFoundError,
    SelfEscalationError,
    SystemRoleImmutableError,
)
from iam_platform.application.tenant_authz.ports import TenantUowFactory
from iam_platform.core.clock import Clock
from iam_platform.domain.shared.policies import can_assign_role
from iam_platform.domain.tenant_authz.entities import TenantRole

_MANAGE_ROLES_PERMISSION = "tenant.roles.manage"


@dataclass(frozen=True, slots=True)
class CreateCustomRoleCommand:
    actor_user_id: str
    tenant_id: str
    code: str
    name: str
    description: str | None
    rank: int
    permission_codes: list[str]


class CreateCustomRole:
    def __init__(self, uow_factory: TenantUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: CreateCustomRoleCommand) -> UUID:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id, tenant_id) as uow:
            actor_state = await compute_effective_tenant_state(uow, tenant_id, actor_id, now=now)
            if actor_state is None or _MANAGE_ROLES_PERMISSION not in actor_state.permissions:
                raise PermissionDeniedError(_MANAGE_ROLES_PERMISSION)

            if await uow.tenant_roles.get_by_code(tenant_id, command.code) is not None:
                raise DuplicateRoleCodeError(command.code)

            requested_codes = set(command.permission_codes)
            catalog = {p.code: p for p in await uow.tenant_permissions.list_by_codes(requested_codes)}
            missing_from_catalog = requested_codes - catalog.keys()
            if missing_from_catalog:
                raise PermissionNotFoundError(", ".join(sorted(missing_from_catalog)))

            not_customizable = {
                code for code, perm in catalog.items() if not perm.tenant_customizable
            }
            if not_customizable:
                raise PermissionNotTenantCustomizableError(", ".join(sorted(not_customizable)))

            # A custom role's permission set is itself gated by the
            # self-escalation guard, using the role's own (not-yet-persisted)
            # rank -- this is the same "cannot grant what you don't hold"
            # check, applied at role-definition time rather than assignment
            # time, so a role can never be *defined* with more power than its
            # creator has, regardless of who it's later assigned to.
            violations = can_assign_role(
                actor_effective_permissions=actor_state.permissions,
                actor_highest_rank=actor_state.highest_role_rank,
                is_self_assignment=False,
                target_role_rank=command.rank,
                target_role_permission_codes=frozenset(requested_codes),
            )
            if violations:
                raise SelfEscalationError(violations)

            role = TenantRole(
                id=uuid4(),
                tenant_id=tenant_id,
                code=command.code,
                name=command.name,
                description=command.description,
                is_system=False,
                rank=command.rank,
                created_by_user_id=actor_id,
                created_at=now,
                updated_at=now,
            )
            await uow.tenant_roles.add(role)
            for code in requested_codes:
                await uow.tenant_permissions.assign_to_role(role_id=role.id, permission_code=code, now=now)

            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=None,
                tenant_id=tenant_id,
                action="tenant_authz.custom_role_created",
                resource_type="tenant_role",
                resource_id=role.id,
                result="success",
                metadata={"code": command.code, "permission_codes": sorted(requested_codes)},
            )
            return role.id


@dataclass(frozen=True, slots=True)
class RolePermissionCommand:
    actor_user_id: str
    tenant_id: str
    role_code: str
    permission_code: str


class AddPermissionToRole:
    """Adds one permission to an existing custom role.

    A role's permission set could previously only be set at creation time --
    fixing a role you got slightly wrong meant deleting it (which isn't
    supported either) and starting over. Gated exactly like role creation:
    the self-escalation check runs against the role's *resulting* permission
    set (existing + this one), not just the one being added, because what
    matters is whether the role ends up granting more than the actor holds.
    """

    def __init__(self, uow_factory: TenantUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: RolePermissionCommand) -> None:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id, tenant_id) as uow:
            actor_state = await compute_effective_tenant_state(uow, tenant_id, actor_id, now=now)
            if actor_state is None or _MANAGE_ROLES_PERMISSION not in actor_state.permissions:
                raise PermissionDeniedError(_MANAGE_ROLES_PERMISSION)

            role = await uow.tenant_roles.get_by_code(tenant_id, command.role_code)
            if role is None:
                raise RoleNotFoundError(command.role_code)
            if role.is_system:
                raise SystemRoleImmutableError(command.role_code)

            permission = await uow.tenant_permissions.get_by_code(command.permission_code)
            if permission is None:
                raise PermissionNotFoundError(command.permission_code)
            if not permission.tenant_customizable:
                raise PermissionNotTenantCustomizableError(command.permission_code)

            existing_codes = (
                await uow.tenant_permissions.get_role_permission_codes({role.id})
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

            await uow.tenant_permissions.assign_to_role(
                role_id=role.id, permission_code=command.permission_code, now=now
            )
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=None,
                tenant_id=tenant_id,
                action="tenant_authz.role_permission_added",
                resource_type="tenant_role",
                resource_id=role.id,
                result="success",
                metadata={"permission_code": command.permission_code},
            )


class RemovePermissionFromRole:
    """Removes one permission from an existing custom role.

    No self-escalation check: taking power away from a role can never grant
    the actor anything, so the only gate is holding `tenant.roles.manage`
    itself. System roles are still protected -- same reasoning as adding.
    """

    def __init__(self, uow_factory: TenantUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: RolePermissionCommand) -> None:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)

        async with self._uow_factory(actor_id, tenant_id) as uow:
            actor_state = await compute_effective_tenant_state(
                uow, tenant_id, actor_id, now=self._clock.now()
            )
            if actor_state is None or _MANAGE_ROLES_PERMISSION not in actor_state.permissions:
                raise PermissionDeniedError(_MANAGE_ROLES_PERMISSION)

            role = await uow.tenant_roles.get_by_code(tenant_id, command.role_code)
            if role is None:
                raise RoleNotFoundError(command.role_code)
            if role.is_system:
                raise SystemRoleImmutableError(command.role_code)

            await uow.tenant_permissions.revoke_from_role(
                role_id=role.id, permission_code=command.permission_code
            )
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=None,
                tenant_id=tenant_id,
                action="tenant_authz.role_permission_removed",
                resource_type="tenant_role",
                resource_id=role.id,
                result="success",
                metadata={"permission_code": command.permission_code},
            )
