"""Explicit allow/deny overrides -- docs/06-authorization-model.md,
docs/14-schema-tenant-authorization.md. An ALLOW override is treated exactly
like granting a single-permission role for self-escalation purposes; a DENY
override can never escalate anything, so it skips that check.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from iam_platform.application.tenant_authz.effective_permissions import compute_effective_tenant_state
from iam_platform.application.tenant_authz.exceptions import (
    MembershipNotFoundError,
    PermissionDeniedError,
    PermissionNotFoundError,
    SelfEscalationError,
)
from iam_platform.application.tenant_authz.ports import TenantUowFactory
from iam_platform.core.clock import Clock
from iam_platform.domain.shared.policies import can_assign_role
from iam_platform.domain.tenant_authz.entities import (
    AuthorizationOverride,
    OverrideEffect,
    OverrideScope,
    OverrideSubjectType,
)

_MANAGE_ROLES_PERMISSION = "tenant.roles.manage"


@dataclass(frozen=True, slots=True)
class CreateAuthorizationOverrideCommand:
    actor_user_id: str
    tenant_id: str
    target_membership_id: str
    permission_code: str
    effect: str  # "allow" | "deny"
    reason: str
    expires_at: datetime | None = None


class CreateAuthorizationOverride:
    def __init__(self, uow_factory: TenantUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: CreateAuthorizationOverrideCommand) -> UUID:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        target_membership_id = UUID(command.target_membership_id)
        effect = OverrideEffect(command.effect)
        now = self._clock.now()

        async with self._uow_factory(actor_id, tenant_id) as uow:
            actor_state = await compute_effective_tenant_state(uow, tenant_id, actor_id, now=now)
            if actor_state is None or _MANAGE_ROLES_PERMISSION not in actor_state.permissions:
                raise PermissionDeniedError(_MANAGE_ROLES_PERMISSION)

            permission = await uow.tenant_permissions.get_by_code(command.permission_code)
            if permission is None:
                raise PermissionNotFoundError(command.permission_code)

            target_membership = await uow.tenant_memberships.get_by_id(target_membership_id)
            if target_membership is None or target_membership.tenant_id != tenant_id:
                raise MembershipNotFoundError(command.target_membership_id)

            if effect == OverrideEffect.ALLOW:
                violations = can_assign_role(
                    actor_effective_permissions=actor_state.permissions,
                    actor_highest_rank=actor_state.highest_role_rank,
                    is_self_assignment=target_membership_id == actor_state.membership_id,
                    target_role_rank=0,
                    target_role_permission_codes=frozenset({command.permission_code}),
                )
                if violations:
                    raise SelfEscalationError(violations)

            override = AuthorizationOverride(
                id=uuid4(),
                scope=OverrideScope.TENANT,
                tenant_id=tenant_id,
                subject_type=OverrideSubjectType.MEMBERSHIP,
                subject_id=target_membership_id,
                tenant_permission_id=permission.id,
                effect=effect,
                reason=command.reason,
                created_by_user_id=actor_id,
                expires_at=command.expires_at,
                created_at=now,
            )
            await uow.authorization_overrides.add(override)
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=target_membership.user_id,
                tenant_id=tenant_id,
                action="tenant_authz.override_created",
                resource_type="authorization_override",
                resource_id=override.id,
                result="success",
                metadata={"permission_code": command.permission_code, "effect": command.effect},
            )
            return override.id


@dataclass(frozen=True, slots=True)
class RevokeAuthorizationOverrideCommand:
    actor_user_id: str
    tenant_id: str
    override_id: str


class RevokeAuthorizationOverride:
    def __init__(self, uow_factory: TenantUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: RevokeAuthorizationOverrideCommand) -> None:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id, tenant_id) as uow:
            actor_state = await compute_effective_tenant_state(uow, tenant_id, actor_id, now=now)
            if actor_state is None or _MANAGE_ROLES_PERMISSION not in actor_state.permissions:
                raise PermissionDeniedError(_MANAGE_ROLES_PERMISSION)

            await uow.authorization_overrides.revoke(UUID(command.override_id), now=now)
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=None,
                tenant_id=tenant_id,
                action="tenant_authz.override_revoked",
                resource_type="authorization_override",
                resource_id=UUID(command.override_id),
                result="success",
            )
