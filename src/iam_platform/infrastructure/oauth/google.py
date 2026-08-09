"""Google OIDC login -- docs/05-authentication-flows.md.

Implements ``application.identity.ports.OAuthProvider`` directly (rather than
via a separate ``infrastructure.oauth.base`` Protocol) since the Protocol
itself has to live in ``application`` for ``application.identity.oauth_login``
to depend on it without importing ``infrastructure`` -- see
docs/20-dependency-rules.md.
"""

from __future__ import annotations

from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWKClient

from iam_platform.application.identity.ports import OAuthProfile
from iam_platform.core.config import OAuthProviderSettings
from iam_platform.core.errors import TokenInvalidError

_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
_JWKS_URI = "https://www.googleapis.com/oauth2/v3/certs"
_ISSUER = "https://accounts.google.com"


class GoogleOAuthProvider:
    provider_name = "google"

    def __init__(self, settings: OAuthProviderSettings, http_client: httpx.AsyncClient) -> None:
        self._settings = settings
        self._http = http_client
        # PyJWKClient caches the fetched key set internally after first use.
        self._jwk_client = PyJWKClient(_JWKS_URI)

    def build_authorization_url(self, *, state: str, nonce: str, code_challenge: str) -> str:
        params = {
            "client_id": self._settings.client_id,
            "redirect_uri": self._settings.redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return f"{_AUTH_ENDPOINT}?{urlencode(params)}"

    async def exchange_code(
        self, *, code: str, code_verifier: str, expected_nonce: str
    ) -> OAuthProfile:
        response = await self._http.post(
            _TOKEN_ENDPOINT,
            data={
                "code": code,
                "client_id": self._settings.client_id,
                "client_secret": self._settings.client_secret.get_secret_value(),
                "redirect_uri": self._settings.redirect_uri,
                "grant_type": "authorization_code",
                "code_verifier": code_verifier,
            },
        )
        response.raise_for_status()
        id_token = response.json()["id_token"]

        # NOTE: PyJWKClient's key fetch is a synchronous HTTP call under the
        # hood. It's cached after the first lookup, so in steady state this is
        # a local cache hit; a colder-path optimization (pre-warming the JWKS
        # cache asynchronously at startup) is a reasonable follow-up but not
        # required for correctness.
        signing_key = self._jwk_client.get_signing_key_from_jwt(id_token)
        try:
            claims = jwt.decode(
                id_token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self._settings.client_id,
                issuer=_ISSUER,
            )
        except jwt.InvalidTokenError as exc:
            raise TokenInvalidError("google id_token failed verification") from exc

        if claims.get("nonce") != expected_nonce:
            raise TokenInvalidError("google id_token nonce mismatch")

        email = claims.get("email") if claims.get("email_verified") else None
        return OAuthProfile(
            provider=self.provider_name,
            subject=claims["sub"],
            email=email,
            raw_profile=claims,
        )
