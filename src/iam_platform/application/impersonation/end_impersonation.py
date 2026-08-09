from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from iam_platform.application.impersonation.exceptions import ImpersonationSessionNotFoundError
from iam_platform.application.platform_authz.ports import PlatformUowFactory
from iam_platform.core.clock import Clock


@dataclass(frozen=True, slots=True)
class EndImpersonationCommand:
    platform_user_id: str
    impersonation_session_id: str


class EndImpersonation:
    def __init__(self, uow_factory: PlatformUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: EndImpersonationCommand) -> None:
        actor_id = UUID(command.platform_user_id)
        session_id = UUID(command.impersonation_session_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id) as uow:
            session = await uow.impersonation_sessions.get_by_id(session_id)
            if session is None or session.platform_user_id != actor_id:
                raise ImpersonationSessionNotFoundError

            if session.ended_at is None:
                session.end(now=now)
                await uow.impersonation_sessions.save(session)

            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=session.target_user_id,
                tenant_id=session.tenant_id,
                action="platform_authz.impersonation_ended",
                resource_type="impersonation_session",
                resource_id=session.id,
                result="success",
            )
