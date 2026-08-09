"""Refresh-token rotation with reuse detection -- docs/05-authentication-flows.md."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

from iam_platform.application.identity.dto import TokenPair
from iam_platform.application.identity.exceptions import (
    InvalidOrExpiredTokenError,
    RefreshReuseDetectedError,
)
from iam_platform.application.identity.ports import IdentityUnitOfWork, JwtIssuer
from iam_platform.core.clock import Clock
from iam_platform.core.security_tokens import generate_opaque_token, hash_token
from iam_platform.domain.identity.entities import RefreshToken

from .session_issuance import REFRESH_TOKEN_TTL


@dataclass(frozen=True, slots=True)
class RefreshSessionCommand:
    refresh_token: str
    ip: str | None = None
    user_agent: str | None = None


class RefreshSession:
    def __init__(
        self,
        uow_factory: Callable[[], IdentityUnitOfWork],
        jwt_issuer: JwtIssuer,
        clock: Clock,
        access_token_ttl_seconds: int,
    ) -> None:
        self._uow_factory = uow_factory
        self._jwt_issuer = jwt_issuer
        self._clock = clock
        self._access_token_ttl_seconds = access_token_ttl_seconds

    async def execute(self, command: RefreshSessionCommand) -> TokenPair:
        now = self._clock.now()
        token_hash = hash_token(command.refresh_token)

        # `reuse_detected` is deliberately handled by falling out of the `async
        # with` block NORMALLY (no exception raised inside it) and only raising
        # afterward. Raising from inside the block would make
        # SqlUnitOfWork.__aexit__ roll back the transaction -- which would
        # silently undo the very revocation writes that are the whole point of
        # detecting reuse. This only bit in integration testing against a real
        # transactional database; the in-memory fakes have no rollback
        # semantics, so unit tests couldn't have caught it.
        reuse_detected = False

        async with self._uow_factory() as uow:
            token = await uow.refresh_tokens.get_by_token_hash(token_hash)
            if token is None:
                raise InvalidOrExpiredTokenError

            if token.was_already_rotated:
                await uow.refresh_tokens.revoke_family(
                    token.family_id, reason="reuse_detected", now=now
                )
                await uow.sessions.revoke_all_for_user(token.user_id, reason="reuse_detected", now=now)
                await uow.security_events.record(
                    user_id=token.user_id,
                    tenant_id=None,
                    event_type="refresh_reuse_detected",
                    severity="critical",
                    details={"family_id": str(token.family_id)},
                    ip=command.ip,
                    user_agent=command.user_agent,
                )
                await uow.audit.record(
                    actor_user_id=None,
                    effective_user_id=token.user_id,
                    tenant_id=None,
                    action="identity.refresh_token_reuse_detected",
                    resource_type="refresh_token",
                    resource_id=token.id,
                    result="denied",
                    ip=command.ip,
                    user_agent=command.user_agent,
                )
                reuse_detected = True
                token_pair = None
            else:
                if not token.is_active or token.is_expired(now=now):
                    raise InvalidOrExpiredTokenError

                session = await uow.sessions.get_by_id(token.session_id)
                user = await uow.users.get_by_id(token.user_id)
                if session is None or user is None or not session.is_valid(
                    current_security_stamp=user.security_stamp
                ):
                    raise InvalidOrExpiredTokenError

                new_raw_refresh = generate_opaque_token()
                new_token = RefreshToken(
                    id=uuid4(),
                    user_id=token.user_id,
                    session_id=token.session_id,
                    family_id=token.family_id,
                    token_hash=hash_token(new_raw_refresh),
                    issued_at=now,
                    expires_at=now + REFRESH_TOKEN_TTL,
                )
                token.rotate(replacement_id=new_token.id, now=now)

                # new_token must be INSERTed before the old token's UPDATE can
                # set replaced_by_id to point at it -- the FK would otherwise
                # reject a reference to a row that doesn't exist yet (also
                # only caught by integration testing).
                await uow.refresh_tokens.add(new_token)
                await uow.refresh_tokens.save(token)

                session.touch(now=now)
                await uow.sessions.save(session)

                amr = ["pwd", "mfa"] if session.mfa_verified else ["pwd"]
                issued = self._jwt_issuer.issue_access_token(
                    user_id=user.id,
                    session_id=session.id,
                    amr=amr,
                    auth_time=session.created_at,
                    now=now,
                )
                token_pair = TokenPair(
                    access_token=issued.token,
                    refresh_token=new_raw_refresh,
                    expires_in=self._access_token_ttl_seconds,
                )

        if reuse_detected:
            raise RefreshReuseDetectedError
        assert token_pair is not None
        return token_pair
