"""AuthN dependency -- verifies the bearer JWT *and* that the session behind it
is still live, per docs/06-authorization-model.md step 1 of the per-request
authorization chain.

**The freshness check is not optional.** A valid signature only proves the token
was issued by this service before it expired; it says nothing about whether the
session was since revoked or the account suspended. docs/05-authentication-flows.md
is explicit that bumping `user.security_stamp` must make "any access token issued
before this moment fail a freshness check ... even before it naturally expires",
and that is what `logout-all`, password change, account suspension and account
deletion all rely on to take effect immediately.

That check was specified but never implemented: this dependency used to return
as soon as the signature verified. Every one of those operations therefore left
the existing access token working until its natural 10-15 minute expiry --
suspending an account for cause did not actually stop the person mid-session.
Found by asserting it in a test rather than by reading the code.

It costs one indexed lookup per authenticated request. docs/06 anticipates
folding this into the (still-deferred) Redis permission cache; until that
exists, correctness is bought with a round-trip rather than skipped.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from iam_platform.api.deps.container import AppContainer
from iam_platform.application.identity.ports import AccessTokenClaims
from iam_platform.core.errors import TokenExpiredError, TokenInvalidError

_bearer_scheme = HTTPBearer(auto_error=False)


def get_container(request: Request) -> AppContainer:
    container = request.app.state.container
    assert isinstance(container, AppContainer)
    return container


async def get_current_claims(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    container: AppContainer = Depends(get_container),
) -> AccessTokenClaims:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token"
        )
    try:
        claims = container.jwt_verifier.verify(credentials.credentials)
    except TokenExpiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="access token expired"
        ) from exc
    except TokenInvalidError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid access token"
        ) from exc

    await _assert_session_is_live(container, claims)
    return claims


async def _assert_session_is_live(container: AppContainer, claims: AccessTokenClaims) -> None:
    """Rejects a structurally valid token whose session or account is no longer good.

    Every failure is reported as the same opaque 401. Distinguishing "revoked"
    from "suspended" from "deleted" would tell a holder of a stolen token what
    happened to the account they took it from.
    """
    async with container.uow_factory() as uow:
        user = await uow.users.get_by_id(claims.user_id)
        # `can_authenticate`, not `is_active`: the same question `LoginUser`
        # asks when it issues the token. Asking a stricter one here refused
        # every request from an account that had just signed in successfully.
        if user is None or not user.can_authenticate:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="session is no longer valid"
            )

        session = await uow.sessions.get_by_id(claims.session_id)
        # `is_valid` covers both halves: not revoked, and the stamp snapshot
        # taken at sign-in still matches the user's current one.
        if session is None or not session.is_valid(current_security_stamp=user.security_stamp):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="session is no longer valid"
            )
