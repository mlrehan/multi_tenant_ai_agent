"""Shared "create a session + token pair" logic used by login and MFA verification.

Factored out because both ``LoginUser`` (no-MFA path) and ``VerifyMfaChallenge``
(post-MFA path) need to do exactly this, and duplicating it would risk the two
paths drifting apart on something security-relevant (e.g. what goes in ``amr``).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

from iam_platform.application.identity.dto import TokenPair
from iam_platform.application.identity.ports import IdentityUnitOfWork, JwtIssuer
from iam_platform.core.security_tokens import generate_opaque_token, hash_token
from iam_platform.domain.identity.entities import RefreshToken, Session, User

REFRESH_TOKEN_TTL = timedelta(days=30)


async def create_session_and_tokens(
    uow: IdentityUnitOfWork,
    jwt_issuer: JwtIssuer,
    *,
    user: User,
    amr: list[str],
    now: datetime,
    ip: str | None,
    user_agent: str | None,
    access_token_ttl_seconds: int,
) -> TokenPair:
    session = Session(
        id=uuid4(),
        user_id=user.id,
        created_at=now,
        last_seen_at=now,
        ip=ip,
        user_agent=user_agent,
        security_stamp_snapshot=user.security_stamp,
        mfa_verified="mfa" in amr,
    )
    await uow.sessions.add(session)

    raw_refresh = generate_opaque_token()
    refresh_token = RefreshToken(
        id=uuid4(),
        user_id=user.id,
        session_id=session.id,
        family_id=uuid4(),
        token_hash=hash_token(raw_refresh),
        issued_at=now,
        expires_at=now + REFRESH_TOKEN_TTL,
    )
    await uow.refresh_tokens.add(refresh_token)

    issued = jwt_issuer.issue_access_token(
        user_id=user.id,
        session_id=session.id,
        amr=amr,
        auth_time=now,
        now=now,
    )
    return TokenPair(
        access_token=issued.token,
        refresh_token=raw_refresh,
        expires_in=access_token_ttl_seconds,
    )
