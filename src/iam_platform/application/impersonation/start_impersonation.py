"""Platform-initiated support impersonation -- docs/06-authorization-model.md §5.

The issued token's ``sub`` is the TARGET user (so downstream tenant/permission
resolution uses the target's own, real, tenant-scoped access -- never
platform permissions), while ``act`` preserves the original platform
identity for every subsequent audit entry, per docs/05-authentication-flows.md's
token shape.

**Scope note (Phase 6):** the impersonation token's ``session_id`` claim is
the ``impersonation_sessions.id`` itself, not a row in the identity module's
``sessions`` table -- creating a full identity session would require this
use case to write through both the platform and identity Units of Work in
one logical operation, which is a bigger cross-module transaction pattern
than Phase 6 needs to prove the impersonation model works. Revocation is
checked via ``impersonation_sessions.ended_at``/``expires_at`` directly.
Similarly, audit records written by *other* use cases during an active
impersonation session don't yet resolve ``actor_user_id`` back to the
platform identity from the ``act`` claim -- that wiring lands with Phase 7's
AI-resource endpoints, the first place impersonation is used operationally.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from iam_platform.application.identity.ports import IssuedAccessToken, JwtIssuer
from iam_platform.application.impersonation.exceptions import (
    ImpersonationDeniedError,
    ImpersonationTargetNotFoundError,
)
from iam_platform.application.platform_authz.effective_permissions import (
    compute_effective_platform_state,
)
from iam_platform.application.platform_authz.ports import PlatformUowFactory
from iam_platform.core.clock import Clock
from iam_platform.domain.platform_authz.entities import ImpersonationSession

_IMPERSONATE_PERMISSION = "platform.support.impersonate"
IMPERSONATION_TTL = timedelta(minutes=30)


@dataclass(frozen=True, slots=True)
class StartImpersonationCommand:
    platform_user_id: str
    tenant_id: str
    target_user_id: str
    reason: str
    ip: str | None = None


class StartImpersonation:
    def __init__(
        self, uow_factory: PlatformUowFactory, jwt_issuer: JwtIssuer, clock: Clock
    ) -> None:
        self._uow_factory = uow_factory
        self._jwt_issuer = jwt_issuer
        self._clock = clock

    async def execute(self, command: StartImpersonationCommand) -> IssuedAccessToken:
        actor_id = UUID(command.platform_user_id)
        target_id = UUID(command.target_user_id)
        tenant_id = UUID(command.tenant_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id) as uow:
            state = await compute_effective_platform_state(uow, actor_id, now=now)
            if _IMPERSONATE_PERMISSION not in state.permissions:
                raise ImpersonationDeniedError(_IMPERSONATE_PERMISSION)

            target_membership = await uow.tenant_memberships.get_by_tenant_and_user(
                tenant_id, target_id
            )
            if target_membership is None or not target_membership.is_active:
                raise ImpersonationTargetNotFoundError

            session = ImpersonationSession(
                id=uuid4(),
                platform_user_id=actor_id,
                target_user_id=target_id,
                tenant_id=tenant_id,
                reason=command.reason,
                started_at=now,
                expires_at=now + IMPERSONATION_TTL,
                ip=command.ip,
            )
            await uow.impersonation_sessions.add(session)
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=target_id,
                tenant_id=tenant_id,
                action="platform_authz.impersonation_started",
                resource_type="impersonation_session",
                resource_id=session.id,
                result="success",
                ip=command.ip,
                metadata={"reason": command.reason},
            )

        return self._jwt_issuer.issue_access_token(
            user_id=target_id,
            session_id=session.id,
            amr=["impersonation"],
            auth_time=now,
            now=now,
            actor={"sub": str(actor_id), "imp_sid": str(session.id)},
        )
