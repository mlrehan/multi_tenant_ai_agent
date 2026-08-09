from datetime import UTC, datetime, timedelta

import pytest

from iam_platform.application.identity.exceptions import (
    InvalidOrExpiredTokenError,
    WeakPasswordError,
)
from iam_platform.application.identity.register_user import (
    RegisterUser,
    RegisterUserCommand,
    VerifyEmail,
    VerifyEmailCommand,
)
from iam_platform.core.clock import FixedClock
from iam_platform.core.config import PasswordPolicySettings

from .fakes import FakeEmailSender, FakeIdentityUnitOfWork, FakePasswordHasher

NOW = datetime(2026, 1, 1, tzinfo=UTC)
POLICY = PasswordPolicySettings(min_length=12, max_length=256)


def _build_register_use_case(uow: FakeIdentityUnitOfWork, email_sender: FakeEmailSender) -> RegisterUser:
    return RegisterUser(uow, FakePasswordHasher(), email_sender, POLICY, FixedClock(NOW))


class TestRegisterUser:
    async def test_creates_user_identity_credential_and_sends_verification_email(self) -> None:
        uow = FakeIdentityUnitOfWork()
        email_sender = FakeEmailSender()
        use_case = _build_register_use_case(uow, email_sender)

        await use_case.execute(
            RegisterUserCommand(email="new.user@example.com", password="Correct-Horse9")
        )

        assert len(uow.users.by_id) == 1
        user = next(iter(uow.users.by_id.values()))
        assert str(user.email) == "new.user@example.com"
        assert len(uow.identities.by_id) == 1
        assert len(uow.credentials.by_identity_id) == 1
        assert len(uow.email_verifications.by_id) == 1
        assert len(email_sender.verification_emails) == 1
        assert email_sender.verification_emails[0][0] == "new.user@example.com"

    async def test_duplicate_email_is_a_silent_no_op(self) -> None:
        uow = FakeIdentityUnitOfWork()
        email_sender = FakeEmailSender()
        use_case = _build_register_use_case(uow, email_sender)

        await use_case.execute(
            RegisterUserCommand(email="dup@example.com", password="Correct-Horse9")
        )
        await use_case.execute(
            RegisterUserCommand(email="dup@example.com", password="Different-Horse9")
        )

        # Still exactly one user and one verification email sent -- the second
        # call must not reveal (via side effects or exceptions) that the
        # account already existed.
        assert len(uow.users.by_id) == 1
        assert len(email_sender.verification_emails) == 1

    async def test_weak_password_is_rejected_before_any_write(self) -> None:
        uow = FakeIdentityUnitOfWork()
        use_case = _build_register_use_case(uow, FakeEmailSender())

        with pytest.raises(WeakPasswordError):
            await use_case.execute(RegisterUserCommand(email="weak@example.com", password="short"))

        assert len(uow.users.by_id) == 0


class TestVerifyEmail:
    async def test_valid_token_activates_the_user(self) -> None:
        uow = FakeIdentityUnitOfWork()
        email_sender = FakeEmailSender()
        register = _build_register_use_case(uow, email_sender)
        await register.execute(RegisterUserCommand(email="verify@example.com", password="Correct-Horse9"))

        raw_token = email_sender.verification_emails[0][1]
        verify = VerifyEmail(uow, FixedClock(NOW))
        await verify.execute(VerifyEmailCommand(token=raw_token))

        user = next(iter(uow.users.by_id.values()))
        assert user.email_verified_at == NOW
        assert user.status.value == "active"

    async def test_unknown_token_raises(self) -> None:
        uow = FakeIdentityUnitOfWork()
        verify = VerifyEmail(uow, FixedClock(NOW))

        with pytest.raises(InvalidOrExpiredTokenError):
            await verify.execute(VerifyEmailCommand(token="not-a-real-token"))

    async def test_expired_token_raises(self) -> None:
        uow = FakeIdentityUnitOfWork()
        email_sender = FakeEmailSender()
        register = _build_register_use_case(uow, email_sender)
        await register.execute(RegisterUserCommand(email="expired@example.com", password="Correct-Horse9"))
        raw_token = email_sender.verification_emails[0][1]

        far_future = FixedClock(NOW + timedelta(hours=25))  # TTL is 24h
        verify = VerifyEmail(uow, far_future)

        with pytest.raises(InvalidOrExpiredTokenError):
            await verify.execute(VerifyEmailCommand(token=raw_token))
