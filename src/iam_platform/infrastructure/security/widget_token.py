"""Session tokens for public chat-widget visitors.

**A visitor is not a user, and this token says so.** Every other token in this
platform names a `users` row and carries a session that can be revoked, an
authentication method, and an authentication time. A person reading a tenant's
public help page has none of those and should never be given a token shaped as
if they did -- a token with a `sub` naming a user is one bug away from being
treated as that user.

So these claims name a *widget*: which tenant, which knowledge base, which
origin the session was minted for. There is no user id in them at all, which
means no code path can mistakenly resolve one.

**The audience is the boundary.** `PyJwtService.verify` pins
`audience=settings.audience`; this verifier pins
`audience=settings.widget_audience`. A widget token presented to an
authenticated console endpoint fails signature verification outright, and a
console access token presented here fails the same way. Neither is a check
someone has to remember to write -- it falls out of how PyJWT validates.

The signing key is shared deliberately. The separation that carries weight is
the audience claim; a second keypair would double the key-rotation surface
(already a known gap, docs/22) without adding a guarantee.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import jwt

from iam_platform.core.config import JwtSettings
from iam_platform.core.errors import TokenExpiredError, TokenInvalidError


@dataclass(frozen=True, slots=True)
class WidgetSessionClaims:
    """What a visitor's session is scoped to.

    Note what is absent: no user id, no membership, no permissions. A visitor
    has no identity in this platform and no authority beyond asking one
    knowledge base a question.
    """

    widget_id: UUID
    tenant_id: UUID
    knowledge_base_id: UUID
    session_id: UUID
    #: The origin this session was minted for, carried so it can be re-checked
    #: on every request rather than only at issuance.
    origin: str


@dataclass(frozen=True, slots=True)
class IssuedWidgetSession:
    token: str
    session_id: UUID
    expires_at: datetime


class WidgetTokenService:
    def __init__(self, settings: JwtSettings) -> None:
        self._settings = settings

    def issue(
        self,
        *,
        widget_id: UUID,
        tenant_id: UUID,
        knowledge_base_id: UUID,
        origin: str,
        now: datetime,
    ) -> IssuedWidgetSession:
        session_id = uuid4()
        expires_at = now + timedelta(seconds=self._settings.widget_session_ttl_seconds)
        claims: dict[str, object] = {
            # `sub` is the *widget*, never a user. A visitor has no account,
            # and a token whose subject looked like one would invite code
            # elsewhere to treat it as one.
            "sub": str(widget_id),
            "sid": str(session_id),
            "jti": str(uuid4()),
            "iss": self._settings.issuer,
            "aud": self._settings.widget_audience,
            "iat": int(now.timestamp()),
            "exp": int(expires_at.timestamp()),
            "tid": str(tenant_id),
            "kb": str(knowledge_base_id),
            "org": origin,
        }
        token = jwt.encode(
            claims,
            self._settings.private_key_pem.get_secret_value(),
            algorithm=self._settings.algorithm,
        )
        return IssuedWidgetSession(
            token=token, session_id=session_id, expires_at=expires_at
        )

    def verify(self, token: str) -> WidgetSessionClaims:
        try:
            payload = jwt.decode(
                token,
                self._settings.public_key_pem,
                algorithms=[self._settings.algorithm],
                issuer=self._settings.issuer,
                # The boundary. A console access token carries the *other*
                # audience and is rejected here, as this one is there.
                audience=self._settings.widget_audience,
                leeway=self._settings.clock_skew_seconds,
            )
        except jwt.ExpiredSignatureError as exc:
            raise TokenExpiredError from exc
        except jwt.InvalidTokenError as exc:
            raise TokenInvalidError from exc

        try:
            return WidgetSessionClaims(
                widget_id=UUID(payload["sub"]),
                tenant_id=UUID(payload["tid"]),
                knowledge_base_id=UUID(payload["kb"]),
                session_id=UUID(payload["sid"]),
                origin=str(payload["org"]),
            )
        except (KeyError, ValueError) as exc:
            # A correctly-signed token missing a claim this code depends on is
            # not merely malformed -- it means something is minting tokens with
            # this key and a different shape. Refused rather than partially
            # honoured.
            raise TokenInvalidError from exc
