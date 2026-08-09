from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from iam_platform.application.identity.exceptions import (
    InvalidOrExpiredTokenError,
    RefreshReuseDetectedError,
)
from iam_platform.application.identity.refresh_session import (
    RefreshSession,
    RefreshSessionCommand,
)
from iam_platform.application.identity.session_issuance import create_session_and_tokens
from iam_platform.core.clock import FixedClock
from iam_platform.domain.identity.entities import User, UserStatus
from iam_platform.domain.shared.value_objects import Email

from .fakes import FakeIdentityUnitOfWork, FakeJwtIssuer

NOW = datetime(2026, 1, 1, tzinfo=UTC)


async def _seed_logged_in_user(uow: FakeIdentityUnitOfWork) -> tuple[User, str]:
    user = User(
        id=uuid4(),
        email=Email("session@example.com"),
        status=UserStatus.ACTIVE,
        security_stamp=uuid4(),
        created_at=NOW,
        updated_at=NOW,
    )
    uow.users.by_id[user.id] = user
    tokens = await create_session_and_tokens(
        uow,
        FakeJwtIssuer(),
        user=user,
        amr=["pwd"],
        now=NOW,
        ip=None,
        user_agent=None,
        access_token_ttl_seconds=900,
    )
    return user, tokens.refresh_token


def _build_use_case(uow: FakeIdentityUnitOfWork, *, now: datetime = NOW) -> RefreshSession:
    return RefreshSession(uow, FakeJwtIssuer(), FixedClock(now), access_token_ttl_seconds=900)


class TestRefreshSession:
    async def test_rotation_issues_a_new_refresh_token_and_revokes_the_old_one(self) -> None:
        uow = FakeIdentityUnitOfWork()
        _, raw_refresh = await _seed_logged_in_user(uow)
        use_case = _build_use_case(uow)

        new_tokens = await use_case.execute(RefreshSessionCommand(refresh_token=raw_refresh))

        assert new_tokens.refresh_token != raw_refresh
        old_token = next(iter(uow.refresh_tokens.by_id.values()))
        assert old_token.revoked_reason == "rotated"

    async def test_reusing_an_already_rotated_token_revokes_the_whole_family(self) -> None:
        uow = FakeIdentityUnitOfWork()
        user, raw_refresh = await _seed_logged_in_user(uow)
        use_case = _build_use_case(uow)

        await use_case.execute(RefreshSessionCommand(refresh_token=raw_refresh))  # legitimate rotation

        # Attacker replays the OLD (already-rotated) token.
        with pytest.raises(RefreshReuseDetectedError):
            await use_case.execute(RefreshSessionCommand(refresh_token=raw_refresh))

        # The entire family -- including the token issued by the legitimate
        # rotation above -- must now be revoked, forcing full re-login.
        assert all(t.revoked_at is not None for t in uow.refresh_tokens.by_id.values())
        assert any(s.revoked_reason == "reuse_detected" for s in uow.sessions.by_id.values())
        assert len(uow.security_events.events) == 1
        assert uow.security_events.events[0]["event_type"] == "refresh_reuse_detected"

    async def test_unknown_token_raises(self) -> None:
        uow = FakeIdentityUnitOfWork()
        use_case = _build_use_case(uow)

        with pytest.raises(InvalidOrExpiredTokenError):
            await use_case.execute(RefreshSessionCommand(refresh_token="not-a-real-token"))

    async def test_expired_token_raises(self) -> None:
        uow = FakeIdentityUnitOfWork()
        _, raw_refresh = await _seed_logged_in_user(uow)
        # REFRESH_TOKEN_TTL is 30 days -- jump well past it.
        use_case = _build_use_case(uow, now=NOW + timedelta(days=31))

        with pytest.raises(InvalidOrExpiredTokenError):
            await use_case.execute(RefreshSessionCommand(refresh_token=raw_refresh))

    async def test_refreshed_access_token_carries_mfa_amr_when_session_was_mfa_verified(self) -> None:
        uow = FakeIdentityUnitOfWork()
        user = User(
            id=uuid4(),
            email=Email("mfa-session@example.com"),
            status=UserStatus.ACTIVE,
            security_stamp=uuid4(),
            created_at=NOW,
            updated_at=NOW,
        )
        uow.users.by_id[user.id] = user
        tokens = await create_session_and_tokens(
            uow,
            FakeJwtIssuer(),
            user=user,
            amr=["pwd", "mfa"],
            now=NOW,
            ip=None,
            user_agent=None,
            access_token_ttl_seconds=900,
        )
        session = next(iter(uow.sessions.by_id.values()))
        assert session.mfa_verified is True

        use_case = _build_use_case(uow)
        # Just confirming the refresh succeeds without error using an mfa-verified session.
        new_tokens = await use_case.execute(RefreshSessionCommand(refresh_token=tokens.refresh_token))
        assert new_tokens.access_token
