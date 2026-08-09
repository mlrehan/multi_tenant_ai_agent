"""Facebook Login -- docs/05-authentication-flows.md.

Facebook Login is OAuth 2.0, not OIDC: there is no signed id_token to verify.
The trust anchor is instead the direct server-to-server TLS call to Facebook's
token and Graph API endpoints -- the access token is exchanged and immediately
used to fetch the profile in the same request, never accepted from the client.
"""

from __future__ import annotations

from urllib.parse import urlencode

import httpx

from iam_platform.application.identity.ports import OAuthProfile
from iam_platform.core.config import OAuthProviderSettings

_AUTH_ENDPOINT = "https://www.facebook.com/v19.0/dialog/oauth"
_TOKEN_ENDPOINT = "https://graph.facebook.com/v19.0/oauth/access_token"
_ME_ENDPOINT = "https://graph.facebook.com/v19.0/me"


class FacebookOAuthProvider:
    provider_name = "facebook"

    def __init__(self, settings: OAuthProviderSettings, http_client: httpx.AsyncClient) -> None:
        self._settings = settings
        self._http = http_client

    def build_authorization_url(self, *, state: str, nonce: str, code_challenge: str) -> str:
        # `nonce` is accepted for signature symmetry with the OIDC providers but
        # unused -- CSRF protection relies entirely on `state`, per Facebook's
        # documented (non-OIDC) integration pattern.
        params = {
            "client_id": self._settings.client_id,
            "redirect_uri": self._settings.redirect_uri,
            "response_type": "code",
            "scope": "email public_profile",
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return f"{_AUTH_ENDPOINT}?{urlencode(params)}"

    async def exchange_code(
        self, *, code: str, code_verifier: str, expected_nonce: str
    ) -> OAuthProfile:
        token_response = await self._http.get(
            _TOKEN_ENDPOINT,
            params={
                "client_id": self._settings.client_id,
                "client_secret": self._settings.client_secret.get_secret_value(),
                "redirect_uri": self._settings.redirect_uri,
                "code": code,
                "code_verifier": code_verifier,
            },
        )
        token_response.raise_for_status()
        access_token = token_response.json()["access_token"]

        profile_response = await self._http.get(
            _ME_ENDPOINT,
            params={"fields": "id,email", "access_token": access_token},
        )
        profile_response.raise_for_status()
        profile = profile_response.json()

        return OAuthProfile(
            provider=self.provider_name,
            subject=profile["id"],
            email=profile.get("email"),
            raw_profile=profile,
        )
