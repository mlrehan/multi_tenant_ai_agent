"""RS256/EdDSA JWT access-token issuance + verification -- docs/05-authentication-flows.md.

Claim shape matches the token structure documented there exactly: ``sub``,
``sid``, ``jti``, ``iss``, ``aud``, ``iat``, ``exp``, ``auth_time``, ``amr``,
optional ``act``. No roles/permissions/tenant claims -- those are resolved
per-request (docs/06-authorization-model.md), not embedded here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import jwt

from iam_platform.application.identity.ports import AccessTokenClaims, IssuedAccessToken
from iam_platform.core.config import JwtSettings
from iam_platform.core.errors import TokenExpiredError, TokenInvalidError


class PyJwtService:
    """Implements both ``JwtIssuer`` (application port) and the verification
    side used by ``api/deps/authn.py``."""

    def __init__(self, settings: JwtSettings) -> None:
        self._settings = settings

    def issue_access_token(
        self,
        *,
        user_id: UUID,
        session_id: UUID,
        amr: list[str],
        auth_time: datetime,
        now: datetime,
        actor: dict[str, Any] | None = None,
    ) -> IssuedAccessToken:
        token_id = uuid4()
        expires_at = now + timedelta(seconds=self._settings.access_token_ttl_seconds)
        claims: dict[str, object] = {
            "sub": str(user_id),
            "sid": str(session_id),
            "jti": str(token_id),
            "iss": self._settings.issuer,
            "aud": self._settings.audience,
            "iat": int(now.timestamp()),
            "exp": int(expires_at.timestamp()),
            "auth_time": int(auth_time.timestamp()),
            "amr": amr,
        }
        if actor is not None:
            claims["act"] = actor

        token = jwt.encode(
            claims,
            self._settings.private_key_pem.get_secret_value(),
            algorithm=self._settings.algorithm,
        )
        return IssuedAccessToken(token=token, token_id=token_id, expires_at=expires_at)

    def verify(self, token: str) -> AccessTokenClaims:
        try:
            payload = jwt.decode(
                token,
                self._settings.public_key_pem,
                algorithms=[self._settings.algorithm],
                issuer=self._settings.issuer,
                audience=self._settings.audience,
                leeway=self._settings.clock_skew_seconds,
            )
        except jwt.ExpiredSignatureError as exc:
            raise TokenExpiredError from exc
        except jwt.InvalidTokenError as exc:
            raise TokenInvalidError from exc

        return AccessTokenClaims(
            user_id=UUID(payload["sub"]),
            session_id=UUID(payload["sid"]),
            token_id=UUID(payload["jti"]),
            amr=payload.get("amr", []),
            auth_time=datetime.fromtimestamp(payload["auth_time"], tz=UTC),
            actor=payload.get("act"),
        )
