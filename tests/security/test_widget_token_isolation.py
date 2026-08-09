"""A widget session token must never authenticate to the console API.

This is the security property Phase 13 Part B rests on. Every other endpoint in
this platform requires a token belonging to a real `users` row with a
membership and resolved permissions. The public widget surface hands a token to
**anyone who visits a tenant's website** — so if that token could satisfy
`PyJwtService.verify`, a stranger reading a public help page would hold a
credential accepted by the tenant administration API.

Both directions are tested, because a boundary that holds one way and not the
other is not a boundary. Nothing here stubs the verifiers; both are driven with
real signing keys, because the property under test is exactly what PyJWT does
with the `aud` claim.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from iam_platform.core.config import JwtSettings
from iam_platform.core.errors import TokenExpiredError, TokenInvalidError
from iam_platform.infrastructure.security.jwt_service import PyJwtService
from iam_platform.infrastructure.security.widget_token import WidgetTokenService

pytestmark = pytest.mark.unit

# Real current time, not a fixed date. PyJWT validates `exp` against the
# wall clock, so a token minted at a hardcoded past date expires before the
# audience is ever examined -- which made the two boundary tests below fail
# for entirely the wrong reason. Had they asserted on a broader exception they
# would have *passed* for the wrong reason instead, which is worse.
def _now() -> datetime:
    return datetime.now(UTC)


def _settings() -> JwtSettings:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return JwtSettings(private_key_pem=private_pem, public_key_pem=public_pem)


def _widget_token(settings: JwtSettings, **overrides: object) -> str:
    service = WidgetTokenService(settings)
    return service.issue(
        widget_id=overrides.get("widget_id", uuid4()),  # type: ignore[arg-type]
        tenant_id=overrides.get("tenant_id", uuid4()),  # type: ignore[arg-type]
        knowledge_base_id=overrides.get("knowledge_base_id", uuid4()),  # type: ignore[arg-type]
        origin=str(overrides.get("origin", "https://tenant.example")),
        now=overrides.get("now", _now()),  # type: ignore[arg-type]
    ).token


class TestTheBoundaryHoldsBothWays:
    def test_a_widget_token_is_refused_by_the_console_verifier(self) -> None:
        """The one that matters most.

        If this ever passes, every visitor to a tenant's public website is
        holding a credential the tenant administration API accepts.
        """
        settings = _settings()

        with pytest.raises(TokenInvalidError):
            PyJwtService(settings).verify(_widget_token(settings))

    def test_a_console_access_token_is_refused_by_the_widget_verifier(self) -> None:
        """The other direction. A boundary that holds one way is not a
        boundary -- and a console token accepted here would silently grant a
        real user's session to the public surface's quota and logging."""
        settings = _settings()
        issued = PyJwtService(settings).issue_access_token(
            user_id=uuid4(),
            session_id=uuid4(),
            amr=["pwd"],
            auth_time=_now(),
            now=_now(),
        )

        with pytest.raises(TokenInvalidError):
            WidgetTokenService(settings).verify(issued.token)

    def test_the_two_audiences_are_actually_different(self) -> None:
        """Guards the configuration rather than the code. Setting
        `JWT__WIDGET_AUDIENCE` equal to `JWT__AUDIENCE` would collapse the
        boundary while every other test here still passed."""
        settings = _settings()

        assert settings.widget_audience != settings.audience


class TestWidgetClaimsCarryNoUserIdentity:
    def test_the_claims_have_no_user_field_at_all(self) -> None:
        """Structural. A visitor has no account, and claims with a user field
        are one careless edit away from being resolved as a user."""
        import dataclasses

        from iam_platform.infrastructure.security.widget_token import WidgetSessionClaims

        fields = {f.name for f in dataclasses.fields(WidgetSessionClaims)}
        assert "user_id" not in fields
        assert "membership_id" not in fields
        assert "permissions" not in fields

    def test_a_round_trip_preserves_the_scope(self) -> None:
        """The positive control: without it, a verifier that rejected
        everything would satisfy every refusal test above."""
        settings = _settings()
        widget_id, tenant_id, kb_id = uuid4(), uuid4(), uuid4()

        token = _widget_token(
            settings,
            widget_id=widget_id,
            tenant_id=tenant_id,
            knowledge_base_id=kb_id,
            origin="https://help.tenant.example",
        )
        claims = WidgetTokenService(settings).verify(token)

        assert claims.widget_id == widget_id
        assert claims.tenant_id == tenant_id
        assert claims.knowledge_base_id == kb_id
        assert claims.origin == "https://help.tenant.example"


class TestSessionLifetime:
    def test_an_expired_session_is_refused(self) -> None:
        settings = _settings()
        long_ago = _now() - timedelta(seconds=settings.widget_session_ttl_seconds + 3600)

        with pytest.raises(TokenExpiredError):
            WidgetTokenService(settings).verify(_widget_token(settings, now=long_ago))

    def test_sessions_are_short_lived(self) -> None:
        """A visitor's session lasts a conversation, not a working day -- the
        token lives in a browser on a public page."""
        assert _settings().widget_session_ttl_seconds <= 3600


class TestTamperedTokens:
    def test_a_token_signed_with_another_key_is_refused(self) -> None:
        theirs, ours = _settings(), _settings()

        with pytest.raises(TokenInvalidError):
            WidgetTokenService(ours).verify(_widget_token(theirs))
