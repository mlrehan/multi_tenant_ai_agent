"""Assign/revoke a tenant role on a membership -- gated by both
`tenant.roles.manage` and the self-escalation guard (docs/06-authorization-model.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from iam_platform.application.tenant_authz.effective_permissions import compute_effective_tenant_state
from iam_platform.application.tenant_authz.exceptions import (
    MembershipNotFoundError,
    PermissionDeniedError,
    RoleNotFoundError,
    SelfEscalationError,
)
from iam_platform.application.tenant_authz.ports import TenantUowFactory
from iam_platform.core.clock import Clock
from iam_platform.domain.shared.policies import can_assign_role
from iam_platform.domain.tenant_authz.entities import TenantMembershipRole

_MANAGE_ROLES_PERMISSION = "tenant.roles.manage"


@dataclass(frozen=True, slots=True)
class AssignMembershipRoleCommand:
    actor_user_id: str
    tenant_id: str
    target_membership_id: str
    role_code: str


class AssignMembershipRole:
    def __init__(self, uow_factory: TenantUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: AssignMembershipRoleCommand) -> None:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        target_membership_id = UUID(command.target_membership_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id, tenant_id) as uow:
            actor_state = await compute_effective_tenant_state(uow, tenant_id, actor_id, now=now)
            if actor_state is None or _MANAGE_ROLES_PERMISSION not in actor_state.permissions:
                raise PermissionDeniedError(_MANAGE_ROLES_PERMISSION)

            role = await uow.tenant_roles.get_by_code(tenant_id, command.role_code) or await (
                uow.tenant_roles.get_by_code(None, command.role_code)
            )
            if role is None:
                raise RoleNotFoundError(command.role_code)

            target_membership = await uow.tenant_memberships.get_by_id(target_membership_id)
            if target_membership is None or target_membership.tenant_id != tenant_id:
                raise MembershipNotFoundError(command.target_membership_id)

            role_permission_codes = await uow.tenant_permissions.get_role_permission_codes({role.id})

            violations = can_assign_role(
                actor_effective_permissions=actor_state.permissions,
                actor_highest_rank=actor_state.highest_role_rank,
                is_self_assignment=target_membership_id == actor_state.membership_id,
                target_role_rank=role.rank,
                target_role_permission_codes=frozenset(role_permission_codes.get(role.id, set())),
            )
            if violations:
                raise SelfEscalationError(violations)

            existing = await uow.tenant_membership_roles.get_active(
                membership_id=target_membership_id, role_id=role.id
            )
            if existing is not None:
                return  # idempotent

            assignment = TenantMembershipRole(
                id=uuid4(),
                tenant_id=tenant_id,
                membership_id=target_membership_id,
                role_id=role.id,
                granted_by_user_id=actor_id,
                granted_at=now,
            )
            await uow.tenant_membership_roles.add(assignment)
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=target_membership.user_id,
                tenant_id=tenant_id,
                action="tenant_authz.role_assigned",
                resource_type="tenant_membership_role",
                resource_id=assignment.id,
                result="success",
                metadata={"role_code": command.role_code},
            )


@dataclass(frozen=True, slots=True)
class RevokeMembershipRoleCommand:
    actor_user_id: str
    tenant_id: str
    target_membership_id: str
    role_code: str


class RevokeMembershipRole:
    def __init__(self, uow_factory: TenantUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: RevokeMembershipRoleCommand) -> None:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        target_membership_id = UUID(command.target_membership_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id, tenant_id) as uow:
            actor_state = await compute_effective_tenant_state(uow, tenant_id, actor_id, now=now)
            if actor_state is None or _MANAGE_ROLES_PERMISSION not in actor_state.permissions:
                raise PermissionDeniedError(_MANAGE_ROLES_PERMISSION)

            role = await uow.tenant_roles.get_by_code(tenant_id, command.role_code) or await (
                uow.tenant_roles.get_by_code(None, command.role_code)
            )
            if role is None:
                raise RoleNotFoundError(command.role_code)

            assignment = await uow.tenant_membership_roles.get_active(
                membership_id=target_membership_id, role_id=role.id
            )
            if assignment is None:
                return  # idempotent

            target_membership = await uow.tenant_memberships.get_by_id(target_membership_id)

            assignment.revoke(now=now)
            await uow.tenant_membership_roles.save(assignment)
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=target_membership.user_id if target_membership else None,
                tenant_id=tenant_id,
                action="tenant_authz.role_revoked",
                resource_type="tenant_membership_role",
                resource_id=assignment.id,
                result="success",
                metadata={"role_code": command.role_code},
            )
