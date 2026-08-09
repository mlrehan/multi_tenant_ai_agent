"""Membership lifecycle: suspend / reactivate / revoke -- docs/13-schema-tenant-management.md."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from iam_platform.application.tenancy.exceptions import MembershipNotFoundError, PermissionDeniedError
from iam_platform.application.tenant_authz.effective_permissions import compute_effective_tenant_state
from iam_platform.application.tenant_authz.ports import TenantUowFactory
from iam_platform.core.clock import Clock

_MANAGE_MEMBERS_PERMISSION = "tenant.users.manage"


@dataclass(frozen=True, slots=True)
class MembershipLifecycleCommand:
    actor_user_id: str
    tenant_id: str
    target_membership_id: str
    reason: str = ""


class SuspendMembership:
    def __init__(self, uow_factory: TenantUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: MembershipLifecycleCommand) -> None:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        target_id = UUID(command.target_membership_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id, tenant_id) as uow:
            actor_state = await compute_effective_tenant_state(uow, tenant_id, actor_id, now=now)
            if actor_state is None or _MANAGE_MEMBERS_PERMISSION not in actor_state.permissions:
                raise PermissionDeniedError(_MANAGE_MEMBERS_PERMISSION)

            membership = await uow.tenant_memberships.get_by_id(target_id)
            if membership is None or membership.tenant_id != tenant_id:
                raise MembershipNotFoundError(command.target_membership_id)

            membership.suspend(reason=command.reason, now=now)
            await uow.tenant_memberships.save(membership)
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=membership.user_id,
                tenant_id=tenant_id,
                action="tenancy.membership_suspended",
                resource_type="tenant_membership",
                resource_id=membership.id,
                result="success",
                metadata={"reason": command.reason},
            )


class ReactivateMembership:
    def __init__(self, uow_factory: TenantUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: MembershipLifecycleCommand) -> None:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        target_id = UUID(command.target_membership_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id, tenant_id) as uow:
            actor_state = await compute_effective_tenant_state(uow, tenant_id, actor_id, now=now)
            if actor_state is None or _MANAGE_MEMBERS_PERMISSION not in actor_state.permissions:
                raise PermissionDeniedError(_MANAGE_MEMBERS_PERMISSION)

            membership = await uow.tenant_memberships.get_by_id(target_id)
            if membership is None or membership.tenant_id != tenant_id:
                raise MembershipNotFoundError(command.target_membership_id)

            membership.reactivate(now=now)
            await uow.tenant_memberships.save(membership)
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=membership.user_id,
                tenant_id=tenant_id,
                action="tenancy.membership_reactivated",
                resource_type="tenant_membership",
                resource_id=membership.id,
                result="success",
            )


class RestoreMembership:
    """Reverses a mistaken revocation.

    Same permission as every other membership-lifecycle action
    (`tenant.users.manage`) -- restoring access is exactly as sensitive an
    operation as removing it, so it gets the same gate, not a lighter one.
    """

    def __init__(self, uow_factory: TenantUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: MembershipLifecycleCommand) -> None:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        target_id = UUID(command.target_membership_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id, tenant_id) as uow:
            actor_state = await compute_effective_tenant_state(uow, tenant_id, actor_id, now=now)
            if actor_state is None or _MANAGE_MEMBERS_PERMISSION not in actor_state.permissions:
                raise PermissionDeniedError(_MANAGE_MEMBERS_PERMISSION)

            membership = await uow.tenant_memberships.get_by_id(target_id)
            if membership is None or membership.tenant_id != tenant_id:
                raise MembershipNotFoundError(command.target_membership_id)

            membership.restore(now=now)
            await uow.tenant_memberships.save(membership)
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=membership.user_id,
                tenant_id=tenant_id,
                action="tenancy.membership_restored",
                resource_type="tenant_membership",
                resource_id=membership.id,
                result="success",
            )


class RevokeMembership:
    def __init__(self, uow_factory: TenantUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: MembershipLifecycleCommand) -> None:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        target_id = UUID(command.target_membership_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id, tenant_id) as uow:
            actor_state = await compute_effective_tenant_state(uow, tenant_id, actor_id, now=now)
            if actor_state is None or _MANAGE_MEMBERS_PERMISSION not in actor_state.permissions:
                raise PermissionDeniedError(_MANAGE_MEMBERS_PERMISSION)

            membership = await uow.tenant_memberships.get_by_id(target_id)
            if membership is None or membership.tenant_id != tenant_id:
                raise MembershipNotFoundError(command.target_membership_id)

            membership.revoke(reason=command.reason, now=now)
            await uow.tenant_memberships.save(membership)
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=membership.user_id,
                tenant_id=tenant_id,
                action="tenancy.membership_revoked",
                resource_type="tenant_membership",
                resource_id=membership.id,
                result="success",
                metadata={"reason": command.reason},
            )
