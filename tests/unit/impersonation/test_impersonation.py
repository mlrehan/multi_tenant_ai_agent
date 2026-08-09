"""StartImpersonation/EndImpersonation -- platform-permission-gated support
access, token issued for the TARGET user's identity with the `act` claim
preserving the platform actor (docs/06-authorization-model.md §5)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from iam_platform.application.impersonation.end_impersonation import (
    EndImpersonation,
    EndImpersonationCommand,
)
from iam_platform.application.impersonation.exceptions import (
    ImpersonationDeniedError,
    ImpersonationSessionNotFoundError,
    ImpersonationTargetNotFoundError,
)
from iam_platform.application.impersonation.start_impersonation import (
    StartImpersonation,
    StartImpersonationCommand,
)
from iam_platform.core.clock import FixedClock
from iam_platform.domain.platform_authz.entities import ImpersonationSession, PlatformUserRole
from iam_platform.domain.tenancy.entities import MembershipStatus, TenantMembership
from tests.unit.identity.fakes import FakeJwtIssuer
from tests.unit.tenant_authz.fakes import FakePlatformUnitOfWork, make_platform_role

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _grant_impersonate_permission(uow: FakePlatformUnitOfWork, actor_id) -> None:
    role = make_platform_role(code="support", rank=10, now=NOW)
    uow.platform_roles.by_id[role.id] = role
    uow.platform_permissions.role_permission_codes[role.id] = {"platform.support.impersonate"}
    uow.platform_user_roles.by_id[uuid4()] = PlatformUserRole(
        id=uuid4(), user_id=actor_id, role_id=role.id, granted_by_user_id=actor_id, granted_at=NOW
    )


class TestStartImpersonation:
    async def test_issues_a_token_for_the_target_user_with_act_claim(self) -> None:
        uow = FakePlatformUnitOfWork()
        actor_id, target_id, tenant_id = uuid4(), uuid4(), uuid4()
        _grant_impersonate_permission(uow, actor_id)
        target_membership = TenantMembership(
            id=uuid4(),
            tenant_id=tenant_id,
            user_id=target_id,
            status=MembershipStatus.ACTIVE,
            created_at=NOW,
            updated_at=NOW,
        )
        uow.tenant_memberships.by_id[target_membership.id] = target_membership

        use_case = StartImpersonation(uow, FakeJwtIssuer(), FixedClock(NOW))
        issued = await use_case.execute(
            StartImpersonationCommand(
                platform_user_id=str(actor_id),
                tenant_id=str(tenant_id),
                target_user_id=str(target_id),
                reason="customer support ticket #123",
            )
        )

        assert issued.token == f"jwt-for-{target_id}"  # FakeJwtIssuer echoes the sub it was given
        assert len(uow.impersonation_sessions.by_id) == 1
        assert uow.audit.events[0]["action"] == "platform_authz.impersonation_started"

    async def test_denied_without_impersonate_permission(self) -> None:
        uow = FakePlatformUnitOfWork()
        actor_id, target_id, tenant_id = uuid4(), uuid4(), uuid4()

        use_case = StartImpersonation(uow, FakeJwtIssuer(), FixedClock(NOW))
        with pytest.raises(ImpersonationDeniedError):
            await use_case.execute(
                StartImpersonationCommand(
                    platform_user_id=str(actor_id),
                    tenant_id=str(tenant_id),
                    target_user_id=str(target_id),
                    reason="x",
                )
            )
        assert uow.impersonation_sessions.by_id == {}

    async def test_target_without_active_membership_rejected(self) -> None:
        uow = FakePlatformUnitOfWork()
        actor_id, target_id, tenant_id = uuid4(), uuid4(), uuid4()
        _grant_impersonate_permission(uow, actor_id)
        # no membership seeded at all

        use_case = StartImpersonation(uow, FakeJwtIssuer(), FixedClock(NOW))
        with pytest.raises(ImpersonationTargetNotFoundError):
            await use_case.execute(
                StartImpersonationCommand(
                    platform_user_id=str(actor_id),
                    tenant_id=str(tenant_id),
                    target_user_id=str(target_id),
                    reason="x",
                )
            )


class TestEndImpersonation:
    async def test_ends_an_active_session(self) -> None:
        uow = FakePlatformUnitOfWork()
        actor_id, target_id, tenant_id = uuid4(), uuid4(), uuid4()
        session = ImpersonationSession(
            id=uuid4(),
            platform_user_id=actor_id,
            target_user_id=target_id,
            tenant_id=tenant_id,
            reason="x",
            started_at=NOW,
            expires_at=NOW,
        )
        uow.impersonation_sessions.by_id[session.id] = session

        use_case = EndImpersonation(uow, FixedClock(NOW))
        await use_case.execute(
            EndImpersonationCommand(
                platform_user_id=str(actor_id), impersonation_session_id=str(session.id)
            )
        )
        assert session.ended_at == NOW
        assert uow.audit.events[0]["action"] == "platform_authz.impersonation_ended"

    async def test_ending_someone_elses_session_is_rejected(self) -> None:
        uow = FakePlatformUnitOfWork()
        actor_id, other_platform_user_id, target_id, tenant_id = (
            uuid4(),
            uuid4(),
            uuid4(),
            uuid4(),
        )
        session = ImpersonationSession(
            id=uuid4(),
            platform_user_id=other_platform_user_id,
            target_user_id=target_id,
            tenant_id=tenant_id,
            reason="x",
            started_at=NOW,
            expires_at=NOW,
        )
        uow.impersonation_sessions.by_id[session.id] = session

        use_case = EndImpersonation(uow, FixedClock(NOW))
        with pytest.raises(ImpersonationSessionNotFoundError):
            await use_case.execute(
                EndImpersonationCommand(
                    platform_user_id=str(actor_id), impersonation_session_id=str(session.id)
                )
            )
