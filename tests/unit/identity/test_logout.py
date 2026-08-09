from datetime import UTC, datetime
from uuid import uuid4

import pytest

from iam_platform.application.identity.exceptions import InvalidOrExpiredTokenError
from iam_platform.application.identity.logout import (
    Logout,
    LogoutAllDevices,
    LogoutAllDevicesCommand,
    LogoutCommand,
)
from iam_platform.application.identity.refresh_session import RefreshSession, RefreshSessionCommand
from iam_platform.application.identity.session_issuance import create_session_and_tokens
from iam_platform.core.clock import FixedClock
from iam_platform.domain.identity.entities import User, UserStatus
from iam_platform.domain.shared.value_objects import Email

from .fakes import FakeIdentityUnitOfWork, FakeJwtIssuer

NOW = datetime(2026, 1, 1, tzinfo=UTC)


async def _seed_logged_in_user(uow: FakeIdentityUnitOfWork) -> tuple[User, str]:
    user = User(
        id=uuid4(),
        email=Email("logout@example.com"),
        status=UserStatus.ACTIVE,
        security_stamp=uuid4(),
        created_at=NOW,
        updated_at=NOW,
    )
    uow.users.by_id[user.id] = user
    tokens = await create_session_and_tokens(
        uow, FakeJwtIssuer(), user=user, amr=["pwd"], now=NOW, ip=None, user_agent=None,
        access_token_ttl_seconds=900,
    )
    return user, tokens.refresh_token


class TestLogout:
    async def test_revokes_the_refresh_token_family_and_session(self) -> None:
        uow = FakeIdentityUnitOfWork()
        _, raw_refresh = await _seed_logged_in_user(uow)

        await Logout(uow, FixedClock(NOW)).execute(LogoutCommand(refresh_token=raw_refresh))

        token = next(iter(uow.refresh_tokens.by_id.values()))
        session = next(iter(uow.sessions.by_id.values()))
        assert token.revoked_reason == "logout"
        assert session.revoked_reason == "logout"

    async def test_logged_out_refresh_token_can_no_longer_be_used(self) -> None:
        uow = FakeIdentityUnitOfWork()
        _, raw_refresh = await _seed_logged_in_user(uow)
        await Logout(uow, FixedClock(NOW)).execute(LogoutCommand(refresh_token=raw_refresh))

        refresh_use_case = RefreshSession(uow, FakeJwtIssuer(), FixedClock(NOW), 900)
        # A revoked-for-logout token has revoked_reason="logout", not "rotated",
        # so it is NOT treated as reuse -- it's just correctly rejected as inactive.
        with pytest.raises(InvalidOrExpiredTokenError):
            await refresh_use_case.execute(RefreshSessionCommand(refresh_token=raw_refresh))

    async def test_unknown_refresh_token_is_a_no_op(self) -> None:
        uow = FakeIdentityUnitOfWork()
        # Must not raise.
        await Logout(uow, FixedClock(NOW)).execute(LogoutCommand(refresh_token="never-issued"))


class TestLogoutAllDevices:
    async def test_bumps_security_stamp_and_revokes_everything(self) -> None:
        uow = FakeIdentityUnitOfWork()
        user, _raw_refresh = await _seed_logged_in_user(uow)
        original_stamp = user.security_stamp

        await LogoutAllDevices(uow, FixedClock(NOW)).execute(
            LogoutAllDevicesCommand(user_id=str(user.id))
        )

        updated_user = uow.users.by_id[user.id]
        assert updated_user.security_stamp != original_stamp
        assert all(s.revoked_reason == "logout_all" for s in uow.sessions.by_id.values())
        assert all(t.revoked_reason == "logout_all" for t in uow.refresh_tokens.by_id.values())
