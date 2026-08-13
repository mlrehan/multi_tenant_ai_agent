"""Password login with lockout/rate-limiting and MFA step-up -- docs/05-authentication-flows.md."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import uuid4

from iam_platform.application.identity.dto import LoginResult, LoginStatus
from iam_platform.application.identity.exceptions import (
    AccountLockedError,
    InvalidCredentialsError,
    RateLimitExceededError,
)
from iam_platform.application.identity.ports import (
    IdentityUnitOfWork,
    JwtIssuer,
    MfaChallengeStore,
    PasswordHasher,
    RateLimiter,
)
from iam_platform.application.identity.session_issuance import create_session_and_tokens
from iam_platform.core.clock import Clock
from iam_platform.core.config import LockoutSettings
from iam_platform.domain.identity.entities import (
    AccountLockout,
    IdentityKind,
    User,
)
from iam_platform.domain.identity.policies import (
    compute_lockout_expiry,
    should_lock_account,
)
from iam_platform.domain.shared.value_objects import Email

# A verify() call against this hash always fails, and Argon2id's cost is roughly
# constant regardless of input -- used so "user not found" and "wrong password"
# take a similar amount of time, closing an easy account-enumeration timing gap.
#
# Must be a genuinely valid Argon2id encoding, not just hash-shaped text: a
# malformed hash makes argon2-cffi fail during *decoding*, before it ever
# reaches the expensive comparison step -- so a hand-typed placeholder (the
# previous value here) returns almost instantly, defeating the entire point
# of comparing against a dummy hash. This one is the real output of
# Argon2IdPasswordHasher().hash() for an arbitrary fixed password, so the
# no-such-user path costs the same as a real verification.
_DUMMY_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$TiZx1mS332hx6nTLbycnCA$"
    "009RenymCVhti+X8aE5+ocLC2Q5UFmQv7SNsY2ycT/U"
)


@dataclass(frozen=True, slots=True)
class LoginCommand:
    email: str
    password: str
    ip: str | None = None
    user_agent: str | None = None


class LoginUser:
    def __init__(
        self,
        uow_factory: Callable[[], IdentityUnitOfWork],
        password_hasher: PasswordHasher,
        jwt_issuer: JwtIssuer,
        mfa_challenge_store: MfaChallengeStore,
        rate_limiter: RateLimiter,
        lockout_policy: LockoutSettings,
        clock: Clock,
        access_token_ttl_seconds: int,
    ) -> None:
        self._uow_factory = uow_factory
        self._hasher = password_hasher
        self._jwt_issuer = jwt_issuer
        self._mfa_challenge_store = mfa_challenge_store
        self._rate_limiter = rate_limiter
        self._lockout_policy = lockout_policy
        self._clock = clock
        self._access_token_ttl_seconds = access_token_ttl_seconds

    async def execute(self, command: LoginCommand) -> LoginResult:
        now = self._clock.now()

        allowed = await self._rate_limiter.check_and_increment(
            f"login:{command.email.lower()}", limit=20, window_seconds=900
        )
        if not allowed:
            raise RateLimitExceededError

        email = Email(command.email)

        # `invalid_credentials` is handled by falling out of the `async with`
        # block NORMALLY and raising only afterward -- raising from inside the
        # block would make SqlUnitOfWork.__aexit__ roll back the transaction,
        # silently undoing the failed-attempt record (and any lockout it
        # triggers) that the write was for in the first place. Same fix, same
        # rationale, as the reuse-detection path in refresh_session.py; only
        # caught by integration testing against a real transactional database.
        invalid_credentials = False
        result: LoginResult | None = None

        async with self._uow_factory() as uow:
            user = await uow.users.get_by_email(email)

            if user is not None:
                lockout = await uow.account_lockouts.get_active(user_id=user.id, now=now)
                if lockout is not None and lockout.is_active(now=now):
                    raise AccountLockedError(
                        unlock_at=lockout.unlock_at.isoformat() if lockout.unlock_at else None
                    )

            password_ok = await self._verify_password(uow, user, command.password)

            # Suspended and soft-deleted accounts are refused here.
            #
            # Without this, suspending or deleting an account only invalidated
            # the tokens already issued (via the security-stamp bump): the
            # person could sign straight back in and be handed fresh ones, which
            # made `platform.users.manage` cosmetic. Found by suspending a test
            # account and immediately logging in again.
            #
            # The password is verified *first* and both failures are reported
            # identically, so the response can't be used to learn whether a given
            # address belongs to a suspended account -- the same
            # account-enumeration reasoning as the dummy-hash comparison in
            # `_verify_password`.
            #
            # `PENDING_VERIFICATION` is deliberately NOT refused. This
            # deployment has no email provider (`ConsoleEmailSender` only logs),
            # so verification links are never delivered and gating login on them
            # would lock out every self-registered account with no way back --
            # see docs/22's known gaps. Blocking it belongs with wiring a real
            # provider, not here.
            #
            # The rule itself now lives on the entity as `can_authenticate`,
            # so the per-request freshness check in `api/deps/authn.py` asks
            # the identical question. It previously asked a stricter one, and
            # a self-registered account could sign in here and then be refused
            # on every request it made.
            account_revoked = user is not None and not user.can_authenticate

            if user is None or not password_ok or account_revoked:
                await self._record_failure(uow, command, user, now)
                invalid_credentials = True
            else:
                mfa_methods = await uow.mfa_methods.list_by_user(user.id)
                usable_mfa = [m for m in mfa_methods if m.is_usable]

                if usable_mfa:
                    challenge_id = await self._mfa_challenge_store.create_challenge(
                        user_id=user.id, now=now
                    )
                    result = LoginResult(
                        status=LoginStatus.MFA_REQUIRED, mfa_challenge_id=challenge_id
                    )
                else:
                    user.record_login(now=now)
                    await uow.users.save(user)
                    tokens = await create_session_and_tokens(
                        uow,
                        self._jwt_issuer,
                        user=user,
                        amr=["pwd"],
                        now=now,
                        ip=command.ip,
                        user_agent=command.user_agent,
                        access_token_ttl_seconds=self._access_token_ttl_seconds,
                    )
                    await uow.login_attempts.record(
                        email_attempted=command.email,
                        user_id=user.id,
                        result="success",
                        ip=command.ip,
                        user_agent=command.user_agent,
                        now=now,
                    )
                    await uow.audit.record(
                        actor_user_id=user.id,
                        effective_user_id=user.id,
                        tenant_id=None,
                        action="identity.login",
                        resource_type="user",
                        resource_id=user.id,
                        result="success",
                        ip=command.ip,
                        user_agent=command.user_agent,
                    )
                    result = LoginResult(status=LoginStatus.SUCCESS, tokens=tokens)

        if invalid_credentials:
            raise InvalidCredentialsError
        assert result is not None
        return result

    async def _verify_password(
        self, uow: IdentityUnitOfWork, user: User | None, password: str
    ) -> bool:
        if user is None:
            self._hasher.verify(password, _DUMMY_HASH)
            return False

        identity = await uow.identities.get_by_user_and_kind(user.id, IdentityKind.PASSWORD)
        if identity is None or not identity.is_active:
            self._hasher.verify(password, _DUMMY_HASH)
            return False

        credential = await uow.credentials.get_by_identity_id(identity.id)
        if credential is None:
            self._hasher.verify(password, _DUMMY_HASH)
            return False

        if not self._hasher.verify(password, credential.password_hash):
            return False

        if self._hasher.needs_rehash(credential.password_hash):
            now = self._clock.now()
            credential.change_password(new_hash=self._hasher.hash(password), now=now)
            await uow.credentials.save(credential)

        return True

    async def _record_failure(
        self,
        uow: IdentityUnitOfWork,
        command: LoginCommand,
        user: User | None,
        now: datetime,
    ) -> None:
        await uow.login_attempts.record(
            email_attempted=command.email,
            user_id=user.id if user is not None else None,
            result="invalid_credentials",
            ip=command.ip,
            user_agent=command.user_agent,
            now=now,
        )
        if user is None:
            return

        since = now - timedelta(minutes=self._lockout_policy.window_minutes)
        failure_count = await uow.login_attempts.count_recent_failures(
            email=command.email, since=since
        )
        if should_lock_account(
            failed_attempt_count=failure_count,
            max_failed_attempts=self._lockout_policy.max_failed_attempts,
        ):
            lockout = AccountLockout(
                id=uuid4(),
                user_id=user.id,
                locked_at=now,
                unlock_at=compute_lockout_expiry(
                    now=now, lockout_minutes=self._lockout_policy.lockout_minutes
                ),
                reason="too_many_failed_attempts",
                failed_attempt_count=failure_count,
            )
            await uow.account_lockouts.add(lockout)
            await uow.audit.record(
                actor_user_id=None,
                effective_user_id=user.id,
                tenant_id=None,
                action="identity.account_locked",
                resource_type="user",
                resource_id=user.id,
                result="success",
                ip=command.ip,
                user_agent=command.user_agent,
            )
