"""Logout / logout-all-devices -- docs/05-authentication-flows.md."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

from iam_platform.application.identity.ports import IdentityUnitOfWork
from iam_platform.core.clock import Clock
from iam_platform.core.security_tokens import hash_token


@dataclass(frozen=True, slots=True)
class LogoutCommand:
    refresh_token: str


class Logout:
    """Revokes the presenting session's refresh-token family. The already-issued
    access token is left to expire naturally within its short TTL, per
    docs/05-authentication-flows.md."""

    def __init__(self, uow_factory: Callable[[], IdentityUnitOfWork], clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: LogoutCommand) -> None:
        now = self._clock.now()
        token_hash = hash_token(command.refresh_token)

        async with self._uow_factory() as uow:
            token = await uow.refresh_tokens.get_by_token_hash(token_hash)
            if token is None:
                return  # already logged out / unknown token -- idempotent no-op

            await uow.refresh_tokens.revoke_family(token.family_id, reason="logout", now=now)

            session = await uow.sessions.get_by_id(token.session_id)
            if session is not None:
                session.revoke(reason="logout", now=now)
                await uow.sessions.save(session)

            await uow.audit.record(
                actor_user_id=token.user_id,
                effective_user_id=token.user_id,
                tenant_id=None,
                action="identity.logout",
                resource_type="session",
                resource_id=token.session_id,
                result="success",
            )


@dataclass(frozen=True, slots=True)
class LogoutAllDevicesCommand:
    user_id: str


class LogoutAllDevices:
    """Bumps the user's ``security_stamp`` (invalidating every outstanding access
    token's freshness check) and revokes every session/refresh-token family."""

    def __init__(self, uow_factory: Callable[[], IdentityUnitOfWork], clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: LogoutAllDevicesCommand) -> None:
        user_id = UUID(command.user_id)
        now = self._clock.now()

        async with self._uow_factory() as uow:
            user = await uow.users.get_by_id(user_id)
            if user is None:
                return

            user.bump_security_stamp(new_stamp=uuid4(), now=now)
            await uow.users.save(user)

            await uow.sessions.revoke_all_for_user(user_id, reason="logout_all", now=now)
            # Revoking every session already makes their refresh tokens unusable
            # (RefreshSession checks session.is_valid against the new security_stamp),
            # but families are revoked explicitly too so a stolen-but-unused refresh
            # token fails fast with "revoked" rather than a less obvious 401.
            await uow.refresh_tokens.revoke_all_for_user(user_id, reason="logout_all", now=now)

            await uow.audit.record(
                actor_user_id=user_id,
                effective_user_id=user_id,
                tenant_id=None,
                action="identity.logout_all",
                resource_type="user",
                resource_id=user_id,
                result="success",
            )
