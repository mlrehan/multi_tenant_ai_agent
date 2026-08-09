from datetime import UTC, datetime
from uuid import uuid4

import pytest

from iam_platform.application.identity.dto import LoginStatus
from iam_platform.application.identity.exceptions import MfaChallengeInvalidError, MfaCodeInvalidError
from iam_platform.application.identity.mfa import (
    ConfirmTotpEnrollment,
    ConfirmTotpEnrollmentCommand,
    StartTotpEnrollment,
    StartTotpEnrollmentCommand,
    VerifyMfaChallenge,
    VerifyMfaChallengeCommand,
)
from iam_platform.core.clock import FixedClock
from iam_platform.domain.identity.entities import MfaMethod, MfaMethodType, User, UserStatus
from iam_platform.domain.shared.value_objects import Email

from .fakes import FakeIdentityUnitOfWork, FakeJwtIssuer, FakeMfaChallengeStore, FakeTotpService

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _seed_user(uow: FakeIdentityUnitOfWork) -> User:
    user = User(
        id=uuid4(),
        email=Email("mfa-flow@example.com"),
        status=UserStatus.ACTIVE,
        security_stamp=uuid4(),
        created_at=NOW,
        updated_at=NOW,
    )
    uow.users.by_id[user.id] = user
    return user


class TestTotpEnrollment:
    async def test_start_then_confirm_marks_the_method_verified_and_primary(self) -> None:
        uow = FakeIdentityUnitOfWork()
        user = _seed_user(uow)
        totp = FakeTotpService()

        start = StartTotpEnrollment(uow, totp, FixedClock(NOW))
        started = await start.execute(StartTotpEnrollmentCommand(user_id=str(user.id)))
        assert started.secret == "FAKESECRET"

        confirm = ConfirmTotpEnrollment(uow, totp, FixedClock(NOW))
        code = "TERCESEKAF"  # FakeTotpService.verify() checks code == secret[::-1]
        await confirm.execute(
            ConfirmTotpEnrollmentCommand(
                user_id=str(user.id), mfa_method_id=started.mfa_method_id, code=code
            )
        )

        method = next(iter(uow.mfa_methods.by_id.values()))
        assert method.verified_at == NOW
        assert method.is_primary is True

    async def test_wrong_code_rejects_confirmation(self) -> None:
        uow = FakeIdentityUnitOfWork()
        user = _seed_user(uow)
        totp = FakeTotpService()
        started = await StartTotpEnrollment(uow, totp, FixedClock(NOW)).execute(
            StartTotpEnrollmentCommand(user_id=str(user.id))
        )

        confirm = ConfirmTotpEnrollment(uow, totp, FixedClock(NOW))
        with pytest.raises(MfaCodeInvalidError):
            await confirm.execute(
                ConfirmTotpEnrollmentCommand(
                    user_id=str(user.id), mfa_method_id=started.mfa_method_id, code="000000"
                )
            )


class TestVerifyMfaChallenge:
    async def test_correct_code_completes_login(self) -> None:
        uow = FakeIdentityUnitOfWork()
        user = _seed_user(uow)
        totp = FakeTotpService()
        challenge_store = FakeMfaChallengeStore()

        # Enroll + verify a TOTP method directly (bypassing the enrollment use case for brevity).
        method = MfaMethod(
            id=uuid4(), user_id=user.id, type=MfaMethodType.TOTP,
            secret_encrypted=b"FAKESECRET", verified_at=NOW, created_at=NOW,
        )
        uow.mfa_methods.by_id[method.id] = method

        challenge_id = await challenge_store.create_challenge(user_id=user.id, now=NOW)
        use_case = VerifyMfaChallenge(
            uow, FakeJwtIssuer(), totp, challenge_store, FixedClock(NOW), access_token_ttl_seconds=900
        )

        result = await use_case.execute(
            VerifyMfaChallengeCommand(challenge_id=challenge_id, code="TERCESEKAF")
        )

        assert result.status == LoginStatus.SUCCESS
        assert result.tokens is not None
        assert await challenge_store.get_user_id(challenge_id) is None  # consumed

    async def test_unknown_challenge_raises(self) -> None:
        uow = FakeIdentityUnitOfWork()
        use_case = VerifyMfaChallenge(
            uow, FakeJwtIssuer(), FakeTotpService(), FakeMfaChallengeStore(), FixedClock(NOW), 900
        )
        with pytest.raises(MfaChallengeInvalidError):
            await use_case.execute(VerifyMfaChallengeCommand(challenge_id="nope", code="123456"))

    async def test_wrong_code_records_mfa_failed_attempt_and_raises(self) -> None:
        uow = FakeIdentityUnitOfWork()
        user = _seed_user(uow)
        method = MfaMethod(
            id=uuid4(), user_id=user.id, type=MfaMethodType.TOTP,
            secret_encrypted=b"FAKESECRET", verified_at=NOW, created_at=NOW,
        )
        uow.mfa_methods.by_id[method.id] = method
        challenge_store = FakeMfaChallengeStore()
        challenge_id = await challenge_store.create_challenge(user_id=user.id, now=NOW)

        use_case = VerifyMfaChallenge(
            uow, FakeJwtIssuer(), FakeTotpService(), challenge_store, FixedClock(NOW), 900
        )

        with pytest.raises(MfaCodeInvalidError):
            await use_case.execute(
                VerifyMfaChallengeCommand(challenge_id=challenge_id, code="000000")
            )

        assert uow.login_attempts.records[-1]["result"] == "mfa_failed"
