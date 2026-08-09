"""Password reset request + confirmation -- docs/05-authentication-flows.md."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

from iam_platform.application.identity.exceptions import (
    InvalidOrExpiredTokenError,
    WeakPasswordError,
)
from iam_platform.application.identity.ports import (
    EmailSender,
    IdentityUnitOfWork,
    PasswordHasher,
)
from iam_platform.core.clock import Clock
from iam_platform.core.config import PasswordPolicySettings
from iam_platform.core.security_tokens import generate_opaque_token, hash_token
from iam_platform.domain.identity.entities import IdentityKind, PasswordResetToken
from iam_platform.domain.identity.policies import validate_password
from iam_platform.domain.shared.value_objects import Email

PASSWORD_RESET_TTL = timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class RequestPasswordResetCommand:
    email: str
    ip: str | None = None
    user_agent: str | None = None


class RequestPasswordReset:
    """Same no-signal-on-nonexistent-account shape as ``RegisterUser`` -- always
    returns normally, only actually sends an email if the account exists."""

    def __init__(
        self,
        uow_factory: Callable[[], IdentityUnitOfWork],
        email_sender: EmailSender,
        clock: Clock,
    ) -> None:
        self._uow_factory = uow_factory
        self._email_sender = email_sender
        self._clock = clock

    async def execute(self, command: RequestPasswordResetCommand) -> None:
        now = self._clock.now()
        email = Email(command.email)
        raw_token: str | None = None

        async with self._uow_factory() as uow:
            user = await uow.users.get_by_email(email)
            if user is None:
                return

            raw_token = generate_opaque_token()
            reset_token = PasswordResetToken(
                id=uuid4(),
                user_id=user.id,
                token_hash=hash_token(raw_token),
                expires_at=now + PASSWORD_RESET_TTL,
                created_at=now,
                ip=command.ip,
                user_agent=command.user_agent,
            )
            await uow.password_reset_tokens.add(reset_token)
            await uow.audit.record(
                actor_user_id=user.id,
                effective_user_id=user.id,
                tenant_id=None,
                action="identity.password_reset_requested",
                resource_type="user",
                resource_id=user.id,
                result="success",
                ip=command.ip,
                user_agent=command.user_agent,
            )

        if raw_token is not None:
            await self._email_sender.send_password_reset_email(to=str(email), token=raw_token)


@dataclass(frozen=True, slots=True)
class ResetPasswordCommand:
    token: str
    new_password: str


class ResetPassword:
    def __init__(
        self,
        uow_factory: Callable[[], IdentityUnitOfWork],
        password_hasher: PasswordHasher,
        password_policy: PasswordPolicySettings,
        clock: Clock,
    ) -> None:
        self._uow_factory = uow_factory
        self._hasher = password_hasher
        self._policy = password_policy
        self._clock = clock

    async def execute(self, command: ResetPasswordCommand) -> None:
        violations = validate_password(
            command.new_password,
            min_length=self._policy.min_length,
            max_length=self._policy.max_length,
        )
        if violations:
            raise WeakPasswordError([v.message for v in violations])

        now = self._clock.now()
        token_hash = hash_token(command.token)

        async with self._uow_factory() as uow:
            reset_token = await uow.password_reset_tokens.get_by_token_hash(token_hash)
            if reset_token is None or not reset_token.is_valid(now=now):
                raise InvalidOrExpiredTokenError

            user = await uow.users.get_by_id(reset_token.user_id)
            identity = (
                await uow.identities.get_by_user_and_kind(reset_token.user_id, IdentityKind.PASSWORD)
                if user is not None
                else None
            )
            if user is None or identity is None:
                raise InvalidOrExpiredTokenError

            credential = await uow.credentials.get_by_identity_id(identity.id)
            if credential is None:
                raise InvalidOrExpiredTokenError

            credential.change_password(new_hash=self._hasher.hash(command.new_password), now=now)
            reset_token.mark_used(now=now)
            # A password change forces every other session/refresh token to stop
            # working, same as logout-all -- see docs/05-authentication-flows.md.
            user.bump_security_stamp(new_stamp=uuid4(), now=now)

            await uow.credentials.save(credential)
            await uow.password_reset_tokens.save(reset_token)
            await uow.users.save(user)
            await uow.sessions.revoke_all_for_user(user.id, reason="password_reset", now=now)
            await uow.refresh_tokens.revoke_all_for_user(user.id, reason="password_reset", now=now)
            await uow.audit.record(
                actor_user_id=user.id,
                effective_user_id=user.id,
                tenant_id=None,
                action="identity.password_reset_completed",
                resource_type="user",
                resource_id=user.id,
                result="success",
            )
