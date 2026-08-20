"""Invite a member to a tenant, and accept that invitation -- docs/13-schema-tenant-management.md.

Requested ``role_codes`` are gated by the self-escalation guard exactly like
direct role assignment (docs/06-authorization-model.md): the inviter can
only pre-assign roles whose combined permissions they already hold.

**Design note on acceptance and RLS:** ``tenant_invitations`` is tenant-owned
and RLS-scoped like every other tenant table -- looking a row up by token
hash alone, with no tenant context, isn't possible under RLS (and shouldn't
be worked around by broadening that table's read policy, which would weaken
tenant isolation for a table that doesn't need it). So the accept flow takes
``tenant_id`` explicitly, the same way real invite links carry either a
tenant subdomain or an explicit tenant identifier -- the client learns it
from the invitation URL, not by discovering it from the token.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from iam_platform.application.tenancy.exceptions import (
    InvalidOrExpiredInvitationError,
    InvitationEmailMismatchError,
    MembershipAlreadyExistsError,
    MembershipNotFoundError,
    PermissionDeniedError,
)
from iam_platform.application.tenancy.ports import InvitationEmailSender
from iam_platform.application.tenant_authz.effective_permissions import compute_effective_tenant_state
from iam_platform.application.tenant_authz.exceptions import SelfEscalationError
from iam_platform.application.tenant_authz.ports import TenantUowFactory
from iam_platform.core.clock import Clock
from iam_platform.core.security_tokens import generate_opaque_token, hash_token
from iam_platform.domain.shared.exceptions import InvariantViolationError
from iam_platform.domain.shared.policies import can_assign_role
from iam_platform.domain.shared.value_objects import Email
from iam_platform.domain.tenancy.entities import MembershipStatus, TenantInvitation, TenantMembership
from iam_platform.domain.tenant_authz.entities import TenantMembershipRole

_INVITE_PERMISSION = "tenant.users.invite"
INVITATION_TTL = timedelta(days=7)


async def _guard_entitlement(uow: object, *, tenant_id: object, capability: str) -> None:
    """Refuses an action the platform has not enabled for this tenant.

    Inlined here rather than importing `application.ai_resources.entitlements`:
    that helper is typed against the AI-resource unit of work, and this module
    runs on the tenant one. The read is the same row either way -- and either
    way the tenant cannot write it, because the table grants `app_tenant`
    SELECT only.

    A missing row means the restrictive defaults, never "unlimited": a tenant
    created between a deploy and an operator's first visit must not escape
    every limit because nobody had filled a form in yet.
    """
    from iam_platform.application.ai_resources.exceptions import FeatureNotEntitledError

    stored = await uow.entitlements.get_for_tenant(tenant_id)  # type: ignore[attr-defined]
    allowed = getattr(stored, capability) if stored is not None else False
    if not allowed:
        raise FeatureNotEntitledError(capability)


@dataclass(frozen=True, slots=True)

class InviteMemberCommand:
    actor_user_id: str
    tenant_id: str
    email: str
    role_codes: list[str]


class InviteMember:
    def __init__(
        self, uow_factory: TenantUowFactory, email_sender: InvitationEmailSender, clock: Clock
    ) -> None:
        self._uow_factory = uow_factory
        self._email_sender = email_sender
        self._clock = clock

    async def execute(self, command: InviteMemberCommand) -> None:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        now = self._clock.now()
        email = Email(command.email)
        raw_token: str | None = None

        async with self._uow_factory(actor_id, tenant_id) as uow:
            actor_state = await compute_effective_tenant_state(uow, tenant_id, actor_id, now=now)
            if actor_state is None or _INVITE_PERMISSION not in actor_state.permissions:
                raise PermissionDeniedError(_INVITE_PERMISSION)

            # Permission first, then plan. Existing members are untouched if
            # this is later withdrawn -- only *inviting more* is gated.
            await _guard_entitlement(
                uow, tenant_id=tenant_id, capability="allow_invite_members"
            )

            role_ids: list[UUID] = []
            combined_permissions: set[str] = set()
            highest_rank = 0
            for code in command.role_codes:
                role = await uow.tenant_roles.get_by_code(tenant_id, code) or await (
                    uow.tenant_roles.get_by_code(None, code)
                )
                if role is None:
                    raise InvariantViolationError(f"role not found: {code}")
                role_ids.append(role.id)
                highest_rank = max(highest_rank, role.rank)
                perm_codes = await uow.tenant_permissions.get_role_permission_codes({role.id})
                combined_permissions |= perm_codes.get(role.id, set())

            violations = can_assign_role(
                actor_effective_permissions=actor_state.permissions,
                actor_highest_rank=actor_state.highest_role_rank,
                is_self_assignment=False,
                target_role_rank=highest_rank,
                target_role_permission_codes=frozenset(combined_permissions),
            )
            if violations:
                raise SelfEscalationError(violations)

            existing = await uow.tenant_invitations.get_pending_by_tenant_and_email(
                tenant_id, str(email)
            )
            if existing is None:
                raw_token = generate_opaque_token()
                invitation = TenantInvitation(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    email=email,
                    invited_by_user_id=actor_id,
                    role_ids=role_ids,
                    token_hash=hash_token(raw_token),
                    expires_at=now + INVITATION_TTL,
                    created_at=now,
                )
                await uow.tenant_invitations.add(invitation)
                await uow.audit.record(
                    actor_user_id=actor_id,
                    effective_user_id=None,
                    tenant_id=tenant_id,
                    action="tenancy.member_invited",
                    resource_type="tenant_invitation",
                    resource_id=invitation.id,
                    result="success",
                    metadata={"email": str(email)},
                )
            # else: a pending invitation already exists -- no-op, not an error
            # (matches the no-signal-on-duplicate pattern from identity registration)

        if raw_token is not None:
            await self._email_sender.send_invitation_email(
                to=str(email), token=raw_token, tenant_name=command.tenant_id
            )


@dataclass(frozen=True, slots=True)
class AddMemberDirectlyCommand:
    actor_user_id: str
    tenant_id: str
    target_user_id: str
    role_codes: list[str]
    job_title: str | None = None


class AddMemberDirectly:
    """Adds an already-registered user to this tenant immediately, with no
    invitation step.

    `InviteMember` is the normal path, but it depends on an email actually
    reaching the invitee -- and this deployment has no working email provider
    (`ConsoleEmailSender` only logs). Without this, a tenant administrator
    could create a pending invitation and then had no way to complete it
    short of digging the raw token out of server logs. This is the same
    "administrator vouches directly" shortcut `CreateUser`
    (`application/platform_authz/manage_users.py`) already takes for
    account creation, applied to tenant membership instead: gated by the
    same `tenant.users.invite` permission as a real invitation, and the same
    self-escalation guard on any roles pre-assigned.

    `target_user_id` is a real user id, not an email -- the frontend resolves
    the email search through the platform user directory (`UserPicker`)
    before calling this, so this use case never needs to look outside its own
    tenant-scoped connection to find one.
    """

    def __init__(self, uow_factory: TenantUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: AddMemberDirectlyCommand) -> UUID:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        target_user_id = UUID(command.target_user_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id, tenant_id) as uow:
            actor_state = await compute_effective_tenant_state(uow, tenant_id, actor_id, now=now)
            if actor_state is None or _INVITE_PERMISSION not in actor_state.permissions:
                raise PermissionDeniedError(_INVITE_PERMISSION)

            existing = await uow.tenant_memberships.get_by_tenant_and_user(
                tenant_id, target_user_id
            )
            if existing is not None:
                raise MembershipAlreadyExistsError(str(existing.id))

            role_ids: list[UUID] = []
            combined_permissions: set[str] = set()
            highest_rank = 0
            for code in command.role_codes:
                role = await uow.tenant_roles.get_by_code(tenant_id, code) or await (
                    uow.tenant_roles.get_by_code(None, code)
                )
                if role is None:
                    raise InvariantViolationError(f"role not found: {code}")
                role_ids.append(role.id)
                highest_rank = max(highest_rank, role.rank)
                perm_codes = await uow.tenant_permissions.get_role_permission_codes({role.id})
                combined_permissions |= perm_codes.get(role.id, set())

            if role_ids:
                violations = can_assign_role(
                    actor_effective_permissions=actor_state.permissions,
                    actor_highest_rank=actor_state.highest_role_rank,
                    is_self_assignment=False,
                    target_role_rank=highest_rank,
                    target_role_permission_codes=frozenset(combined_permissions),
                )
                if violations:
                    raise SelfEscalationError(violations)

            membership = TenantMembership(
                id=uuid4(),
                tenant_id=tenant_id,
                user_id=target_user_id,
                status=MembershipStatus.ACTIVE,
                job_title=command.job_title,
                invited_by_user_id=actor_id,
                invited_at=now,
                joined_at=now,
                created_at=now,
                updated_at=now,
            )
            await uow.tenant_memberships.add(membership)

            for role_id in role_ids:
                await uow.tenant_membership_roles.add(
                    TenantMembershipRole(
                        id=uuid4(),
                        tenant_id=tenant_id,
                        membership_id=membership.id,
                        role_id=role_id,
                        granted_by_user_id=actor_id,
                        granted_at=now,
                    )
                )

            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=target_user_id,
                tenant_id=tenant_id,
                action="tenancy.member_added_directly",
                resource_type="tenant_membership",
                resource_id=membership.id,
                result="success",
                metadata={"role_codes": command.role_codes},
            )
            return membership.id


@dataclass(frozen=True, slots=True)
class UpdateMembershipCommand:
    actor_user_id: str
    tenant_id: str
    target_membership_id: str
    job_title: str | None


class UpdateMembership:
    """Edits the free-text fields on a membership -- currently just job
    title. Status transitions (suspend/reactivate/revoke/restore) go through
    `manage_membership.py` instead, since each of those is independently
    audited and permission-checked as a distinct action."""

    def __init__(self, uow_factory: TenantUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: UpdateMembershipCommand) -> None:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        target_id = UUID(command.target_membership_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id, tenant_id) as uow:
            actor_state = await compute_effective_tenant_state(uow, tenant_id, actor_id, now=now)
            if actor_state is None or _INVITE_PERMISSION not in actor_state.permissions:
                raise PermissionDeniedError(_INVITE_PERMISSION)

            membership = await uow.tenant_memberships.get_by_id(target_id)
            if membership is None or membership.tenant_id != tenant_id:
                raise MembershipNotFoundError(command.target_membership_id)

            membership.job_title = command.job_title
            membership.updated_at = now
            await uow.tenant_memberships.save(membership)
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=membership.user_id,
                tenant_id=tenant_id,
                action="tenancy.membership_updated",
                resource_type="tenant_membership",
                resource_id=membership.id,
                result="success",
                metadata={"job_title": command.job_title},
            )


@dataclass(frozen=True, slots=True)
class AcceptInvitationCommand:
    accepting_user_id: str
    accepting_user_email: str
    tenant_id: str
    token: str


class AcceptInvitation:
    def __init__(self, uow_factory: TenantUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: AcceptInvitationCommand) -> UUID:
        user_id = UUID(command.accepting_user_id)
        tenant_id = UUID(command.tenant_id)
        now = self._clock.now()
        token_hash = hash_token(command.token)

        async with self._uow_factory(user_id, tenant_id) as uow:
            invitation = await uow.tenant_invitations.get_by_token_hash(token_hash)
            if (
                invitation is None
                or invitation.tenant_id != tenant_id
                or not invitation.is_valid(now=now)
            ):
                raise InvalidOrExpiredInvitationError

            if str(invitation.email).lower() != command.accepting_user_email.lower():
                raise InvitationEmailMismatchError

            invitation.accept(by_user_id=user_id, now=now)
            await uow.tenant_invitations.save(invitation)

            membership = await uow.tenant_memberships.get_by_tenant_and_user(tenant_id, user_id)
            if membership is None:
                membership = TenantMembership(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    user_id=user_id,
                    status=MembershipStatus.ACTIVE,
                    invited_by_user_id=invitation.invited_by_user_id,
                    invited_at=invitation.created_at,
                    joined_at=now,
                    created_at=now,
                    updated_at=now,
                )
                await uow.tenant_memberships.add(membership)
            elif membership.status == MembershipStatus.INVITED:
                membership.accept_invitation(now=now)
                await uow.tenant_memberships.save(membership)

            for role_id in invitation.role_ids:
                existing_assignment = await uow.tenant_membership_roles.get_active(
                    membership_id=membership.id, role_id=role_id
                )
                if existing_assignment is None:
                    await uow.tenant_membership_roles.add(
                        TenantMembershipRole(
                            id=uuid4(),
                            tenant_id=tenant_id,
                            membership_id=membership.id,
                            role_id=role_id,
                            granted_by_user_id=invitation.invited_by_user_id,
                            granted_at=now,
                        )
                    )

            await uow.audit.record(
                actor_user_id=user_id,
                effective_user_id=user_id,
                tenant_id=tenant_id,
                action="tenancy.invitation_accepted",
                resource_type="tenant_membership",
                resource_id=membership.id,
                result="success",
            )
            return membership.id
