"""TOTP MFA enrollment and the post-login MFA-challenge verification step.

WebAuthn enrollment/verification is intentionally NOT implemented here -- the
``mfa_methods`` table and domain entity already support it, but attestation
verification needs a dedicated library and is deferred to a later pass (see
the Phase 5 scope note in the chat response / CLAUDE.md).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

from iam_platform.application.identity.dto import LoginResult, LoginStatus, TotpEnrollmentStarted
from iam_platform.application.identity.exceptions import (
    MfaChallengeInvalidError,
    MfaCodeInvalidError,
)
from iam_platform.application.identity.ports import (
    IdentityUnitOfWork,
    JwtIssuer,
    MfaChallengeStore,
    TotpService,
)
from iam_platform.application.identity.session_issuance import create_session_and_tokens
from iam_platform.core.clock import Clock
from iam_platform.domain.identity.entities import MfaMethod, MfaMethodType


@dataclass(frozen=True, slots=True)
class StartTotpEnrollmentCommand:
    user_id: str
    label: str | None = None


class StartTotpEnrollment:
    def __init__(
        self,
        uow_factory: Callable[[], IdentityUnitOfWork],
        totp_service: TotpService,
        clock: Clock,
    ) -> None:
        self._uow_factory = uow_factory
        self._totp = totp_service
        self._clock = clock

    async def execute(self, command: StartTotpEnrollmentCommand) -> TotpEnrollmentStarted:
        user_id = UUID(command.user_id)
        now = self._clock.now()
        secret = self._totp.generate_secret()

        async with self._uow_factory() as uow:
            user = await uow.users.get_by_id(user_id)
            if user is None:
                raise MfaChallengeInvalidError

            method = MfaMethod(
                id=uuid4(),
                user_id=user_id,
                type=MfaMethodType.TOTP,
                secret_encrypted=secret.encode("utf-8"),  # encryption-at-rest handled by the
                # infrastructure repository (envelope encryption via KMS), not this use case
                label=command.label,
                created_at=now,
            )
            await uow.mfa_methods.add(method)
            await uow.audit.record(
                actor_user_id=user_id,
                effective_user_id=user_id,
                tenant_id=None,
                action="identity.mfa.enrollment_started",
                resource_type="mfa_method",
                resource_id=method.id,
                result="success",
            )

        return TotpEnrollmentStarted(
            mfa_method_id=str(method.id),
            secret=secret,
            provisioning_uri=self._totp.provisioning_uri(secret=secret, account_email=str(user.email)),
        )


@dataclass(frozen=True, slots=True)
class ConfirmTotpEnrollmentCommand:
    user_id: str
    mfa_method_id: str
    code: str


class ConfirmTotpEnrollment:
    def __init__(
        self,
        uow_factory: Callable[[], IdentityUnitOfWork],
        totp_service: TotpService,
        clock: Clock,
    ) -> None:
        self._uow_factory = uow_factory
        self._totp = totp_service
        self._clock = clock

    async def execute(self, command: ConfirmTotpEnrollmentCommand) -> None:
        now = self._clock.now()

        async with self._uow_factory() as uow:
            method = await uow.mfa_methods.get_by_id(UUID(command.mfa_method_id))
            if (
                method is None
                or str(method.user_id) != command.user_id
                or method.secret_encrypted is None
            ):
                raise MfaChallengeInvalidError

            secret = method.secret_encrypted.decode("utf-8")
            if not self._totp.verify(secret=secret, code=command.code):
                raise MfaCodeInvalidError

            method.mark_verified(now=now)
            if not any(m.is_primary for m in await uow.mfa_methods.list_by_user(method.user_id)):
                method.is_primary = True
            await uow.mfa_methods.save(method)
            await uow.audit.record(
                actor_user_id=method.user_id,
                effective_user_id=method.user_id,
                tenant_id=None,
                action="identity.mfa.enrolled",
                resource_type="mfa_method",
                resource_id=method.id,
                result="success",
            )


@dataclass(frozen=True, slots=True)
class VerifyMfaChallengeCommand:
    challenge_id: str
    code: str
    ip: str | None = None
    user_agent: str | None = None


class VerifyMfaChallenge:
    def __init__(
        self,
        uow_factory: Callable[[], IdentityUnitOfWork],
        jwt_issuer: JwtIssuer,
        totp_service: TotpService,
        mfa_challenge_store: MfaChallengeStore,
        clock: Clock,
        access_token_ttl_seconds: int,
    ) -> None:
        self._uow_factory = uow_factory
        self._jwt_issuer = jwt_issuer
        self._totp = totp_service
        self._mfa_challenge_store = mfa_challenge_store
        self._clock = clock
        self._access_token_ttl_seconds = access_token_ttl_seconds

    async def execute(self, command: VerifyMfaChallengeCommand) -> LoginResult:
        now = self._clock.now()

        user_id = await self._mfa_challenge_store.get_user_id(command.challenge_id)
        if user_id is None:
            raise MfaChallengeInvalidError

        # `code_invalid` is handled the same way as the analogous cases in
        # login_user.py/refresh_session.py: raising from inside the `async
        # with` block would roll back the mfa_failed login_attempts write via
        # SqlUnitOfWork.__aexit__, so the exception is raised only after the
        # block has closed (and therefore committed) normally.
        code_invalid = False
        result: LoginResult | None = None

        async with self._uow_factory() as uow:
            user = await uow.users.get_by_id(user_id)
            if user is None:
                raise MfaChallengeInvalidError

            methods = [m for m in await uow.mfa_methods.list_by_user(user_id) if m.is_usable]
            totp_methods = [m for m in methods if m.type == MfaMethodType.TOTP]

            verified_method = None
            for method in totp_methods:
                if method.secret_encrypted and self._totp.verify(
                    secret=method.secret_encrypted.decode("utf-8"), code=command.code
                ):
                    verified_method = method
                    break

            if verified_method is None:
                await uow.login_attempts.record(
                    email_attempted=str(user.email),
                    user_id=user_id,
                    result="mfa_failed",
                    ip=command.ip,
                    user_agent=command.user_agent,
                    now=now,
                )
                code_invalid = True
            else:
                await self._mfa_challenge_store.consume(command.challenge_id)
                verified_method.record_use(now=now)
                await uow.mfa_methods.save(verified_method)

                user.record_login(now=now)
                await uow.users.save(user)

                tokens = await create_session_and_tokens(
                    uow,
                    self._jwt_issuer,
                    user=user,
                    amr=["pwd", "mfa"],
                    now=now,
                    ip=command.ip,
                    user_agent=command.user_agent,
                    access_token_ttl_seconds=self._access_token_ttl_seconds,
                )
                await uow.login_attempts.record(
                    email_attempted=str(user.email),
                    user_id=user_id,
                    result="success",
                    ip=command.ip,
                    user_agent=command.user_agent,
                    now=now,
                )
                await uow.audit.record(
                    actor_user_id=user_id,
                    effective_user_id=user_id,
                    tenant_id=None,
                    action="identity.login",
                    resource_type="user",
                    resource_id=user_id,
                    result="success",
                    ip=command.ip,
                    user_agent=command.user_agent,
                    metadata={"amr": ["pwd", "mfa"]},
                )
                result = LoginResult(status=LoginStatus.SUCCESS, tokens=tokens)

        if code_invalid:
            raise MfaCodeInvalidError
        assert result is not None
        return result
