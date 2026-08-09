from datetime import UTC, datetime
from uuid import uuid4

import pytest

from iam_platform.application.identity.dto import LoginStatus
from iam_platform.application.identity.exceptions import AccountLockedError, InvalidCredentialsError
from iam_platform.application.identity.login_user import LoginCommand, LoginUser
from iam_platform.core.clock import FixedClock
from iam_platform.core.config import LockoutSettings
from iam_platform.domain.identity.entities import (
    AuthIdentity,
    Credential,
    IdentityKind,
    MfaMethod,
    MfaMethodType,
    User,
    UserStatus,
)
from iam_platform.domain.shared.value_objects import Email

from .fakes import (
    FakeIdentityUnitOfWork,
    FakeJwtIssuer,
    FakeMfaChallengeStore,
    FakePasswordHasher,
    FakeRateLimiter,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)
LOCKOUT = LockoutSettings(max_failed_attempts=3, window_minutes=15, lockout_minutes=15)


def _seed_password_user(uow: FakeIdentityUnitOfWork, *, email: str, password: str) -> User:
    hasher = FakePasswordHasher()
    user = User(
        id=uuid4(),
        email=Email(email),
        status=UserStatus.ACTIVE,
        security_stamp=uuid4(),
        created_at=NOW,
        updated_at=NOW,
    )
    identity = AuthIdentity(id=uuid4(), user_id=user.id, kind=IdentityKind.PASSWORD, created_at=NOW)
    credential = Credential(
        id=uuid4(), identity_id=identity.id, password_hash=hasher.hash(password), password_updated_at=NOW
    )
    uow.users.by_id[user.id] = user
    uow.identities.by_id[identity.id] = identity
    uow.credentials.by_identity_id[identity.id] = credential
    return user


def _build_use_case(uow: FakeIdentityUnitOfWork) -> LoginUser:
    return LoginUser(
        uow,
        FakePasswordHasher(),
        FakeJwtIssuer(),
        FakeMfaChallengeStore(),
        FakeRateLimiter(always_allow=True),
        LOCKOUT,
        FixedClock(NOW),
        access_token_ttl_seconds=900,
    )


class TestLoginUser:
    async def test_successful_login_returns_tokens(self) -> None:
        uow = FakeIdentityUnitOfWork()
        _seed_password_user(uow, email="ok@example.com", password="Correct-Horse9")
        use_case = _build_use_case(uow)

        result = await use_case.execute(LoginCommand(email="ok@example.com", password="Correct-Horse9"))

        assert result.status == LoginStatus.SUCCESS
        assert result.tokens is not None
        assert result.tokens.refresh_token
        assert uow.login_attempts.records[-1]["result"] == "success"

    async def test_wrong_password_raises_and_is_recorded(self) -> None:
        uow = FakeIdentityUnitOfWork()
        _seed_password_user(uow, email="ok@example.com", password="Correct-Horse9")
        use_case = _build_use_case(uow)

        with pytest.raises(InvalidCredentialsError):
            await use_case.execute(LoginCommand(email="ok@example.com", password="wrong-password"))

        assert uow.login_attempts.records[-1]["result"] == "invalid_credentials"

    async def test_unknown_email_raises_invalid_credentials_not_a_distinct_error(self) -> None:
        uow = FakeIdentityUnitOfWork()
        use_case = _build_use_case(uow)

        with pytest.raises(InvalidCredentialsError):
            await use_case.execute(LoginCommand(email="nobody@example.com", password="whatever123!"))

    async def test_account_locks_after_threshold_failed_attempts(self) -> None:
        uow = FakeIdentityUnitOfWork()
        _seed_password_user(uow, email="lockme@example.com", password="Correct-Horse9")
        use_case = _build_use_case(uow)

        for _ in range(LOCKOUT.max_failed_attempts):
            with pytest.raises(InvalidCredentialsError):
                await use_case.execute(
                    LoginCommand(email="lockme@example.com", password="wrong-password")
                )

        # Next attempt, even with the CORRECT password, is blocked by the lockout.
        with pytest.raises(AccountLockedError):
            await use_case.execute(
                LoginCommand(email="lockme@example.com", password="Correct-Horse9")
            )

    async def test_login_with_mfa_enrolled_returns_challenge_not_tokens(self) -> None:
        uow = FakeIdentityUnitOfWork()
        user = _seed_password_user(uow, email="mfa@example.com", password="Correct-Horse9")
        mfa = MfaMethod(
            id=uuid4(),
            user_id=user.id,
            type=MfaMethodType.TOTP,
            secret_encrypted=b"secret",
            verified_at=NOW,
            created_at=NOW,
        )
        uow.mfa_methods.by_id[mfa.id] = mfa
        use_case = _build_use_case(uow)

        result = await use_case.execute(LoginCommand(email="mfa@example.com", password="Correct-Horse9"))

        assert result.status == LoginStatus.MFA_REQUIRED
        assert result.tokens is None
        assert result.mfa_challenge_id is not None
        # No login_attempts row yet -- the attempt isn't "complete" until MFA verifies.
        assert uow.login_attempts.records == []
