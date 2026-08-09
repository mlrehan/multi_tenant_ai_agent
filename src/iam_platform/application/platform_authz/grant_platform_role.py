"""Grant/revoke a platform role -- gated by the self-escalation guard
(docs/06-authorization-model.md): the actor may only grant a role whose
permissions are a subset of their own, and may never elevate their own rank.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from iam_platform.application.platform_authz.effective_permissions import (
    compute_effective_platform_state,
)
from iam_platform.application.platform_authz.exceptions import RoleNotFoundError, SelfEscalationError
from iam_platform.application.platform_authz.ports import PlatformUowFactory
from iam_platform.core.clock import Clock
from iam_platform.domain.platform_authz.entities import PlatformUserRole
from iam_platform.domain.shared.policies import can_assign_role


@dataclass(frozen=True, slots=True)
class GrantPlatformRoleCommand:
    actor_user_id: str
    target_user_id: str
    role_code: str


class GrantPlatformRole:
    def __init__(self, uow_factory: PlatformUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: GrantPlatformRoleCommand) -> None:
        actor_id = UUID(command.actor_user_id)
        target_id = UUID(command.target_user_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id) as uow:
            role = await uow.platform_roles.get_by_code(command.role_code)
            if role is None:
                raise RoleNotFoundError(command.role_code)

            actor_state = await compute_effective_platform_state(uow, actor_id, now=now)
            role_permission_codes = await uow.platform_permissions.get_role_permission_codes({role.id})

            violations = can_assign_role(
                actor_effective_permissions=actor_state.permissions,
                actor_highest_rank=actor_state.highest_role_rank,
                is_self_assignment=actor_id == target_id,
                target_role_rank=role.rank,
                target_role_permission_codes=frozenset(role_permission_codes.get(role.id, set())),
            )
            if violations:
                raise SelfEscalationError(violations)

            existing = await uow.platform_user_roles.get_active(user_id=target_id, role_id=role.id)
            if existing is not None:
                return  # idempotent -- already granted

            assignment = PlatformUserRole(
                id=uuid4(),
                user_id=target_id,
                role_id=role.id,
                granted_by_user_id=actor_id,
                granted_at=now,
            )
            await uow.platform_user_roles.add(assignment)
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=target_id,
                tenant_id=None,
                action="platform_authz.role_granted",
                resource_type="platform_user_role",
                resource_id=assignment.id,
                result="success",
                metadata={"role_code": command.role_code},
            )


@dataclass(frozen=True, slots=True)
class RevokePlatformRoleCommand:
    actor_user_id: str
    target_user_id: str
    role_code: str


class RevokePlatformRole:
    def __init__(self, uow_factory: PlatformUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: RevokePlatformRoleCommand) -> None:
        actor_id = UUID(command.actor_user_id)
        target_id = UUID(command.target_user_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id) as uow:
            role = await uow.platform_roles.get_by_code(command.role_code)
            if role is None:
                raise RoleNotFoundError(command.role_code)

            # Symmetric with grant: an actor may only revoke a role they
            # could themselves have granted. Without this, any bearer token
            # could strip any platform user's role from them -- a privilege-
            # sabotage / denial-of-service path with no gate at all until
            # this fix (found while wiring the admin-panel API surface).
            # The rank half of the guard is a no-op in the revoke direction
            # (revoking never elevates), so this only ever blocks the
            # permission-subset half, which is exactly what's needed here.
            actor_state = await compute_effective_platform_state(uow, actor_id, now=now)
            role_permission_codes = await uow.platform_permissions.get_role_permission_codes({role.id})
            violations = can_assign_role(
                actor_effective_permissions=actor_state.permissions,
                actor_highest_rank=actor_state.highest_role_rank,
                is_self_assignment=actor_id == target_id,
                target_role_rank=role.rank,
                target_role_permission_codes=frozenset(role_permission_codes.get(role.id, set())),
            )
            if violations:
                raise SelfEscalationError(violations)

            assignment = await uow.platform_user_roles.get_active(user_id=target_id, role_id=role.id)
            if assignment is None:
                return  # idempotent -- already not held

            assignment.revoke(by_user_id=actor_id, now=now)
            await uow.platform_user_roles.save(assignment)
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=target_id,
                tenant_id=None,
                action="platform_authz.role_revoked",
                resource_type="platform_user_role",
                resource_id=assignment.id,
                result="success",
                metadata={"role_code": command.role_code},
            )
