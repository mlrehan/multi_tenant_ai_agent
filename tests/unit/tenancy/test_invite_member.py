"""InviteMember/AcceptInvitation -- role pre-assignment on an invitation is
gated by the same self-escalation guard as direct role assignment."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from iam_platform.application.tenancy.exceptions import (
    InvalidOrExpiredInvitationError,
    InvitationEmailMismatchError,
    PermissionDeniedError,
)
from iam_platform.application.tenancy.invite_member import (
    AcceptInvitation,
    AcceptInvitationCommand,
    InviteMember,
    InviteMemberCommand,
)
from iam_platform.application.tenant_authz.exceptions import SelfEscalationError
from iam_platform.core.clock import FixedClock
from iam_platform.core.security_tokens import generate_opaque_token, hash_token
from iam_platform.domain.shared.value_objects import Email
from iam_platform.domain.tenancy.entities import (
    InvitationStatus,
    MembershipStatus,
    TenantInvitation,
    TenantMembership,
)
from iam_platform.domain.tenant_authz.entities import TenantMembershipRole
from tests.unit.tenant_authz.fakes import (
    FakeInvitationEmailSender,
    FakeTenantUnitOfWork,
    make_tenant_role,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _seed_inviter(uow: FakeTenantUnitOfWork, tenant_id, permission_codes: set[str]):
    role = make_tenant_role(tenant_id=None, code="inviter_role", rank=100, now=NOW, is_system=True)
    uow.tenant_roles.by_id[role.id] = role
    uow.tenant_permissions.role_permission_codes[role.id] = permission_codes
    inviter_id = uuid4()
    membership = TenantMembership(
        id=uuid4(),
        tenant_id=tenant_id,
        user_id=inviter_id,
        status=MembershipStatus.ACTIVE,
        created_at=NOW,
        updated_at=NOW,
    )
    uow.tenant_memberships.by_id[membership.id] = membership
    uow.tenant_membership_roles.by_id[uuid4()] = TenantMembershipRole(
        id=uuid4(),
        tenant_id=tenant_id,
        membership_id=membership.id,
        role_id=role.id,
        granted_by_user_id=inviter_id,
        granted_at=NOW,
    )
    return inviter_id


class TestInviteMember:
    async def test_sends_invitation_and_email_when_role_is_within_inviters_grant(self) -> None:
        uow = FakeTenantUnitOfWork()
        tenant_id = uuid4()
        inviter_id = _seed_inviter(
            uow, tenant_id, {"tenant.users.invite", "tenant.resources.read"}
        )
        member_role = make_tenant_role(tenant_id=None, code="member", rank=10, now=NOW, is_system=True)
        uow.tenant_roles.by_id[member_role.id] = member_role
        uow.tenant_permissions.role_permission_codes[member_role.id] = {"tenant.resources.read"}
        sender = FakeInvitationEmailSender()

        use_case = InviteMember(uow, sender, FixedClock(NOW))
        await use_case.execute(
            InviteMemberCommand(
                actor_user_id=str(inviter_id),
                tenant_id=str(tenant_id),
                email="new.hire@example.com",
                role_codes=["member"],
            )
        )

        assert len(uow.tenant_invitations.by_id) == 1
        assert len(sender.sent) == 1
        assert sender.sent[0][0] == "new.hire@example.com"

    async def test_cannot_invite_with_a_role_carrying_permissions_inviter_lacks(self) -> None:
        uow = FakeTenantUnitOfWork()
        tenant_id = uuid4()
        inviter_id = _seed_inviter(uow, tenant_id, {"tenant.users.invite"})
        admin_role = make_tenant_role(tenant_id=None, code="admin", rank=100, now=NOW, is_system=True)
        uow.tenant_roles.by_id[admin_role.id] = admin_role
        uow.tenant_permissions.role_permission_codes[admin_role.id] = {"tenant.billing.manage"}
        sender = FakeInvitationEmailSender()

        use_case = InviteMember(uow, sender, FixedClock(NOW))
        with pytest.raises(SelfEscalationError):
            await use_case.execute(
                InviteMemberCommand(
                    actor_user_id=str(inviter_id),
                    tenant_id=str(tenant_id),
                    email="new.hire@example.com",
                    role_codes=["admin"],
                )
            )
        assert uow.tenant_invitations.by_id == {}
        assert sender.sent == []

    async def test_denied_without_invite_permission(self) -> None:
        uow = FakeTenantUnitOfWork()
        tenant_id = uuid4()
        sender = FakeInvitationEmailSender()
        use_case = InviteMember(uow, sender, FixedClock(NOW))
        with pytest.raises(PermissionDeniedError):
            await use_case.execute(
                InviteMemberCommand(
                    actor_user_id=str(uuid4()),
                    tenant_id=str(tenant_id),
                    email="x@example.com",
                    role_codes=[],
                )
            )

    async def test_duplicate_pending_invitation_is_a_silent_noop(self) -> None:
        uow = FakeTenantUnitOfWork()
        tenant_id = uuid4()
        inviter_id = _seed_inviter(uow, tenant_id, {"tenant.users.invite"})
        existing = TenantInvitation(
            id=uuid4(),
            tenant_id=tenant_id,
            email=Email("dup@example.com"),
            invited_by_user_id=inviter_id,
            role_ids=[],
            token_hash="already-pending",
            expires_at=NOW + timedelta(days=7),
            created_at=NOW,
        )
        uow.tenant_invitations.by_id[existing.id] = existing
        sender = FakeInvitationEmailSender()

        use_case = InviteMember(uow, sender, FixedClock(NOW))
        await use_case.execute(
            InviteMemberCommand(
                actor_user_id=str(inviter_id),
                tenant_id=str(tenant_id),
                email="dup@example.com",
                role_codes=[],
            )
        )
        assert len(uow.tenant_invitations.by_id) == 1  # no second invitation
        assert sender.sent == []  # no email re-sent


class TestAcceptInvitation:
    async def test_creates_membership_and_assigns_invited_roles(self) -> None:
        uow = FakeTenantUnitOfWork()
        tenant_id = uuid4()
        inviter_id = uuid4()
        role = make_tenant_role(tenant_id=None, code="member", rank=10, now=NOW, is_system=True)
        uow.tenant_roles.by_id[role.id] = role

        raw_token = generate_opaque_token()
        invitation = TenantInvitation(
            id=uuid4(),
            tenant_id=tenant_id,
            email=Email("invitee@example.com"),
            invited_by_user_id=inviter_id,
            role_ids=[role.id],
            token_hash=hash_token(raw_token),
            expires_at=NOW + timedelta(days=7),
            created_at=NOW,
        )
        uow.tenant_invitations.by_id[invitation.id] = invitation

        accepting_user_id = uuid4()
        use_case = AcceptInvitation(uow, FixedClock(NOW))
        membership_id = await use_case.execute(
            AcceptInvitationCommand(
                accepting_user_id=str(accepting_user_id),
                accepting_user_email="invitee@example.com",
                tenant_id=str(tenant_id),
                token=raw_token,
            )
        )

        membership = await uow.tenant_memberships.get_by_id(membership_id)
        assert membership is not None
        assert membership.status == MembershipStatus.ACTIVE
        assignment = await uow.tenant_membership_roles.get_active(
            membership_id=membership_id, role_id=role.id
        )
        assert assignment is not None
        assert invitation.status == InvitationStatus.ACCEPTED

    async def test_wrong_email_rejected(self) -> None:
        uow = FakeTenantUnitOfWork()
        tenant_id = uuid4()
        raw_token = generate_opaque_token()
        invitation = TenantInvitation(
            id=uuid4(),
            tenant_id=tenant_id,
            email=Email("invitee@example.com"),
            invited_by_user_id=uuid4(),
            role_ids=[],
            token_hash=hash_token(raw_token),
            expires_at=NOW + timedelta(days=7),
            created_at=NOW,
        )
        uow.tenant_invitations.by_id[invitation.id] = invitation

        use_case = AcceptInvitation(uow, FixedClock(NOW))
        with pytest.raises(InvitationEmailMismatchError):
            await use_case.execute(
                AcceptInvitationCommand(
                    accepting_user_id=str(uuid4()),
                    accepting_user_email="someone.else@example.com",
                    tenant_id=str(tenant_id),
                    token=raw_token,
                )
            )

    async def test_expired_invitation_rejected(self) -> None:
        uow = FakeTenantUnitOfWork()
        tenant_id = uuid4()
        raw_token = generate_opaque_token()
        invitation = TenantInvitation(
            id=uuid4(),
            tenant_id=tenant_id,
            email=Email("invitee@example.com"),
            invited_by_user_id=uuid4(),
            role_ids=[],
            token_hash=hash_token(raw_token),
            expires_at=NOW - timedelta(days=1),
            created_at=NOW - timedelta(days=8),
        )
        uow.tenant_invitations.by_id[invitation.id] = invitation

        use_case = AcceptInvitation(uow, FixedClock(NOW))
        with pytest.raises(InvalidOrExpiredInvitationError):
            await use_case.execute(
                AcceptInvitationCommand(
                    accepting_user_id=str(uuid4()),
                    accepting_user_email="invitee@example.com",
                    tenant_id=str(tenant_id),
                    token=raw_token,
                )
            )
