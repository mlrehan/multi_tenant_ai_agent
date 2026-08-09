"""Registration + email verification -- docs/05-authentication-flows.md."""

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
from iam_platform.domain.identity.entities import (
    AuthIdentity,
    Credential,
    EmailVerification,
    IdentityKind,
    User,
)
from iam_platform.domain.identity.policies import validate_password
from iam_platform.domain.shared.value_objects import Email

EMAIL_VERIFICATION_TTL = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class RegisterUserCommand:
    email: str
    password: str
    ip: str | None = None
    user_agent: str | None = None


class RegisterUser:
    """Always returns normally (no exception, no signal) whether the email was
    already registered or not, to prevent account-enumeration via the
    registration endpoint. Only password-policy violations are surfaced to
    the caller, since they carry no information about existing accounts."""

    def __init__(
        self,
        uow_factory: Callable[[], IdentityUnitOfWork],
        password_hasher: PasswordHasher,
        email_sender: EmailSender,
        password_policy: PasswordPolicySettings,
        clock: Clock,
    ) -> None:
        self._uow_factory = uow_factory
        self._hasher = password_hasher
        self._email_sender = email_sender
        self._policy = password_policy
        self._clock = clock

    async def execute(self, command: RegisterUserCommand) -> None:
        violations = validate_password(
            command.password,
            min_length=self._policy.min_length,
            max_length=self._policy.max_length,
        )
        if violations:
            raise WeakPasswordError([v.message for v in violations])

        email = Email(command.email)
        now = self._clock.now()

        async with self._uow_factory() as uow:
            existing = await uow.users.get_by_email(email)
            if existing is not None:
                await uow.audit.record(
                    actor_user_id=None,
                    effective_user_id=existing.id,
                    tenant_id=None,
                    action="identity.register.duplicate_attempt",
                    result="denied",
                    ip=command.ip,
                    user_agent=command.user_agent,
                )
                return  # generic no-op response -- see docstring

            user = User(
                id=uuid4(),
                email=email,
                security_stamp=uuid4(),
                created_at=now,
                updated_at=now,
            )
            identity = AuthIdentity(id=uuid4(), user_id=user.id, kind=IdentityKind.PASSWORD, created_at=now)
            credential = Credential(
                id=uuid4(),
                identity_id=identity.id,
                password_hash=self._hasher.hash(command.password),
                password_updated_at=now,
            )

            raw_token = generate_opaque_token()
            verification = EmailVerification(
                id=uuid4(),
                user_id=user.id,
                token_hash=hash_token(raw_token),
                purpose="register",
                expires_at=now + EMAIL_VERIFICATION_TTL,
                created_at=now,
                ip=command.ip,
                user_agent=command.user_agent,
            )

            await uow.users.add(user)
            await uow.identities.add(identity)
            await uow.credentials.add(credential)
            await uow.email_verifications.add(verification)
            await uow.audit.record(
                actor_user_id=user.id,
                effective_user_id=user.id,
                tenant_id=None,
                action="identity.register",
                resource_type="user",
                resource_id=user.id,
                result="success",
                ip=command.ip,
                user_agent=command.user_agent,
            )

        await self._email_sender.send_verification_email(to=str(email), token=raw_token)


@dataclass(frozen=True, slots=True)
class VerifyEmailCommand:
    token: str


class VerifyEmail:
    def __init__(self, uow_factory: Callable[[], IdentityUnitOfWork], clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: VerifyEmailCommand) -> None:
        now = self._clock.now()
        token_hash = hash_token(command.token)

        async with self._uow_factory() as uow:
            verification = await uow.email_verifications.get_by_token_hash(token_hash)
            if verification is None or not verification.is_valid(now=now):
                raise InvalidOrExpiredTokenError

            user = await uow.users.get_by_id(verification.user_id)
            if user is None:
                raise InvalidOrExpiredTokenError

            verification.mark_used(now=now)
            user.mark_email_verified(now=now)

            await uow.email_verifications.save(verification)
            await uow.users.save(user)
            await uow.audit.record(
                actor_user_id=user.id,
                effective_user_id=user.id,
                tenant_id=None,
                action="identity.email_verified",
                resource_type="user",
                resource_id=user.id,
                result="success",
            )

