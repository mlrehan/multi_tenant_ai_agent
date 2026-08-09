"""Full-stack integration test: real Postgres, real Redis, real Argon2id
hashing, real RS256 JWT signing -- no fakes anywhere. Exercises the exact
sequence a client would: register, verify email, log in, refresh, log out,
and confirms the reuse-detection path fires against the real database.
"""

from __future__ import annotations

import pytest
from redis.asyncio import Redis

from iam_platform.application.identity.exceptions import (
    AccountLockedError,
    InvalidCredentialsError,
    InvalidOrExpiredTokenError,
    RefreshReuseDetectedError,
)
from iam_platform.application.identity.login_user import LoginCommand, LoginUser
from iam_platform.application.identity.logout import Logout, LogoutCommand
from iam_platform.application.identity.refresh_session import RefreshSession, RefreshSessionCommand
from iam_platform.application.identity.register_user import (
    RegisterUser,
    RegisterUserCommand,
    VerifyEmail,
    VerifyEmailCommand,
)
from iam_platform.core.clock import SystemClock
from iam_platform.domain.shared.value_objects import Email
from iam_platform.infrastructure.cache.mfa_challenge_store import RedisMfaChallengeStore
from iam_platform.infrastructure.cache.rate_limiter import RedisRateLimiter
from iam_platform.infrastructure.cache.redis_client import build_redis_client
from iam_platform.infrastructure.security.jwt_service import PyJwtService
from iam_platform.infrastructure.security.password_hasher import Argon2IdPasswordHasher

pytestmark = pytest.mark.integration


class _CapturingEmailSender:
    def __init__(self) -> None:
        self.last_verification_token: str | None = None

    async def send_verification_email(self, *, to: str, token: str) -> None:
        self.last_verification_token = token

    async def send_password_reset_email(self, *, to: str, token: str) -> None:
        pass


@pytest.fixture
def redis(settings) -> Redis:
    return build_redis_client(settings.redis)


async def test_full_register_verify_login_refresh_reuse_logout_cycle(
    uow_factory, settings, redis: Redis
) -> None:
    clock = SystemClock()
    hasher = Argon2IdPasswordHasher()
    jwt_service = PyJwtService(settings.jwt)
    email_sender = _CapturingEmailSender()

    # 1. Register
    register = RegisterUser(uow_factory, hasher, email_sender, settings.password_policy, clock)
    await register.execute(
        RegisterUserCommand(email="e2e@example.com", password="Correct-Horse-9!")
    )
    assert email_sender.last_verification_token is not None

    # 2. Verify email
    verify = VerifyEmail(uow_factory, clock)
    await verify.execute(VerifyEmailCommand(token=email_sender.last_verification_token))

    async with uow_factory() as uow:
        user = await uow.users.get_by_email(Email("e2e@example.com"))
    assert user is not None
    assert user.status.value == "active"

    # 3. Log in
    login = LoginUser(
        uow_factory,
        hasher,
        jwt_service,
        RedisMfaChallengeStore(redis),
        RedisRateLimiter(redis),
        settings.lockout,
        clock,
        settings.jwt.access_token_ttl_seconds,
    )
    login_result = await login.execute(
        LoginCommand(email="e2e@example.com", password="Correct-Horse-9!")
    )
    assert login_result.tokens is not None

    # The access token must verify against the real signing key and carry the right subject.
    claims = jwt_service.verify(login_result.tokens.access_token)
    assert claims.user_id == user.id
    assert claims.amr == ["pwd"]

    # 4. Refresh -- rotation issues a new pair, old refresh token becomes unusable.
    refresh = RefreshSession(uow_factory, jwt_service, clock, settings.jwt.access_token_ttl_seconds)
    first_refresh_token = login_result.tokens.refresh_token
    rotated = await refresh.execute(RefreshSessionCommand(refresh_token=first_refresh_token))
    assert rotated.refresh_token != first_refresh_token

    # 5. Reuse of the now-rotated original token must be detected and burn the family.
    with pytest.raises(RefreshReuseDetectedError):
        await refresh.execute(RefreshSessionCommand(refresh_token=first_refresh_token))

    # Even the token from the legitimate rotation in step 4 is now dead.
    with pytest.raises(InvalidOrExpiredTokenError):
        await refresh.execute(RefreshSessionCommand(refresh_token=rotated.refresh_token))


async def test_logout_revokes_refresh_token(uow_factory, settings, redis: Redis) -> None:
    clock = SystemClock()
    hasher = Argon2IdPasswordHasher()
    jwt_service = PyJwtService(settings.jwt)
    email_sender = _CapturingEmailSender()

    register = RegisterUser(uow_factory, hasher, email_sender, settings.password_policy, clock)
    await register.execute(RegisterUserCommand(email="logout-e2e@example.com", password="Correct-Horse-9!"))
    await VerifyEmail(uow_factory, clock).execute(
        VerifyEmailCommand(token=email_sender.last_verification_token)
    )

    login = LoginUser(
        uow_factory, hasher, jwt_service, RedisMfaChallengeStore(redis), RedisRateLimiter(redis),
        settings.lockout, clock, settings.jwt.access_token_ttl_seconds,
    )
    result = await login.execute(LoginCommand(email="logout-e2e@example.com", password="Correct-Horse-9!"))
    assert result.tokens is not None

    await Logout(uow_factory, clock).execute(LogoutCommand(refresh_token=result.tokens.refresh_token))

    refresh = RefreshSession(uow_factory, jwt_service, clock, settings.jwt.access_token_ttl_seconds)
    with pytest.raises(InvalidOrExpiredTokenError):
        await refresh.execute(RefreshSessionCommand(refresh_token=result.tokens.refresh_token))


async def test_account_locks_after_threshold_and_lockout_persists_in_db(
    uow_factory, settings, redis: Redis
) -> None:
    """Regression test for the rollback-on-raise bug fixed in login_user.py:
    the failed-attempt records and the resulting lockout row are writes made
    right before `LoginUser.execute` raises -- they must survive against a
    REAL transactional database, not just an in-memory fake."""
    clock = SystemClock()
    hasher = Argon2IdPasswordHasher()
    jwt_service = PyJwtService(settings.jwt)
    email_sender = _CapturingEmailSender()

    register = RegisterUser(uow_factory, hasher, email_sender, settings.password_policy, clock)
    await register.execute(RegisterUserCommand(email="lockout-e2e@example.com", password="Correct-Horse-9!"))
    await VerifyEmail(uow_factory, clock).execute(
        VerifyEmailCommand(token=email_sender.last_verification_token)
    )

    login = LoginUser(
        uow_factory, hasher, jwt_service, RedisMfaChallengeStore(redis), RedisRateLimiter(redis),
        settings.lockout, clock, settings.jwt.access_token_ttl_seconds,
    )

    for _ in range(settings.lockout.max_failed_attempts):
        with pytest.raises(InvalidCredentialsError):
            await login.execute(
                LoginCommand(email="lockout-e2e@example.com", password="wrong-password")
            )

    # A fresh UnitOfWork/connection confirms the lockout was actually
    # committed, not just visible within the same (already-closed) transaction.
    async with uow_factory() as uow:
        user = await uow.users.get_by_email(Email("lockout-e2e@example.com"))
        assert user is not None
        active_lockout = await uow.account_lockouts.get_active(user_id=user.id, now=clock.now())
        assert active_lockout is not None
        failures = await uow.login_attempts.count_recent_failures(
            email="lockout-e2e@example.com", since=clock.now().replace(year=2000)
        )
        assert failures == settings.lockout.max_failed_attempts

    # And the lockout is actually enforced on the next attempt, even with the correct password.
    with pytest.raises(AccountLockedError):
        await login.execute(
            LoginCommand(email="lockout-e2e@example.com", password="Correct-Horse-9!")
        )
