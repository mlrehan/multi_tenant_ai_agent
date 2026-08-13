"""HTTP-level tests against the real FastAPI app -- real Postgres, real Redis,
real crypto. Verifies status codes, response shapes, and header behavior that
only exist at the API layer (nothing here is covered by the application-layer
integration tests in tests/integration/).
"""

from __future__ import annotations

import httpx
import pytest

from .conftest import CapturingEmailSender

pytestmark = pytest.mark.integration


async def test_full_http_auth_cycle(client: httpx.AsyncClient, email_sender: CapturingEmailSender) -> None:
    # Register
    resp = await client.post(
        "/v1/auth/register",
        json={"email": "http-e2e@example.com", "password": "Correct-Horse-9!"},
    )
    assert resp.status_code == 202
    assert email_sender.last_verification_token is not None

    # Verify email
    resp = await client.get(
        "/v1/auth/verify-email", params={"token": email_sender.last_verification_token}
    )
    assert resp.status_code == 200

    # Login
    resp = await client.post(
        "/v1/auth/login",
        json={"email": "http-e2e@example.com", "password": "Correct-Horse-9!"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    access_token = body["tokens"]["access_token"]
    refresh_token = body["tokens"]["refresh_token"]
    assert access_token and refresh_token

    # Access-token-gated endpoint (logout-all) requires a valid bearer token.
    resp = await client.post(
        "/v1/auth/logout-all", headers={"Authorization": f"Bearer {access_token}"}
    )
    assert resp.status_code == 204

    # Refresh token issued before logout-all is now dead (session revoked +
    # security_stamp bumped).
    resp = await client.post("/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert resp.status_code == 400


async def test_login_with_wrong_password_returns_401(client: httpx.AsyncClient, email_sender) -> None:
    await client.post(
        "/v1/auth/register", json={"email": "wrongpw@example.com", "password": "Correct-Horse-9!"}
    )
    await client.get("/v1/auth/verify-email", params={"token": email_sender.last_verification_token})

    resp = await client.post(
        "/v1/auth/login", json={"email": "wrongpw@example.com", "password": "totally-wrong"}
    )
    assert resp.status_code == 401


async def test_an_unverified_account_can_actually_use_its_token(client: httpx.AsyncClient) -> None:
    """The account a real person gets by signing up, used the way they would.

    `LoginUser` deliberately admits `PENDING_VERIFICATION` -- this deployment
    has no email provider, so requiring verification would lock out every
    self-registered user permanently. But the per-request freshness check in
    `api/deps/authn.py` asked `is_active`, which requires `ACTIVE`, so login
    handed back a token that every subsequent request refused with an opaque
    "session is no longer valid". Signing up through the console produced an
    account that appeared to work and then did nothing.

    Nothing caught it because every other test in this file verifies the email
    first -- so the bug lived in the one state the suite never left an account
    in. This test deliberately does **not** verify.
    """
    resp = await client.post(
        "/v1/auth/register",
        json={"email": "unverified-usable@example.com", "password": "Correct-Horse-9!"},
    )
    assert resp.status_code == 202

    resp = await client.post(
        "/v1/auth/login",
        json={"email": "unverified-usable@example.com", "password": "Correct-Horse-9!"},
    )
    assert resp.status_code == 200
    access_token = resp.json()["tokens"]["access_token"]

    # The point of the test: the token login just issued must be accepted.
    resp = await client.get("/v1/auth/me", headers={"Authorization": f"Bearer {access_token}"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["email"] == "unverified-usable@example.com"


async def test_login_with_nonexistent_email_returns_401_not_500(client: httpx.AsyncClient) -> None:
    """Regression test for a real bug found while building the admin-panel
    frontend against this backend: `login_user.py`'s account-enumeration
    timing mitigation compares against a fixed `_DUMMY_HASH` when no user
    matches the email, so the login attempt still costs a real Argon2
    verification. The hand-typed placeholder hash that constant used to hold
    wasn't validly encoded, so argon2-cffi failed at *decode* time with
    `VerificationError: Decoding failed` -- an unhandled exception the
    router had no mapping for, surfacing as a 500 instead of a 401. Only
    reachable with a real Argon2 hasher and no matching user; unit tests
    using fakes structurally can't exercise this path.
    """
    resp = await client.post(
        "/v1/auth/login", json={"email": "no-such-account@example.com", "password": "whatever-123"}
    )
    assert resp.status_code == 401


async def test_register_with_weak_password_returns_422(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/v1/auth/register", json={"email": "weakpw@example.com", "password": "short"}
    )
    assert resp.status_code == 422
    assert "violations" in resp.json()


async def test_protected_endpoint_without_bearer_token_returns_401(client: httpx.AsyncClient) -> None:
    resp = await client.post("/v1/auth/logout-all")
    assert resp.status_code == 401


async def test_security_headers_present_on_every_response(client: httpx.AsyncClient) -> None:
    # Probes `/livez`, not `/healthz`. This test is about the security-header
    # middleware, and `/livez` is the one endpoint deliberately guaranteed
    # not to touch a dependency (api/v1/system/router.py) -- so it can't fail
    # for reasons that have nothing to do with headers.
    #
    # It used to use `/healthz`, which was a stub returning 200 unconditionally
    # when this test was written in Phase 5. Phase 9 made `/healthz`
    # dependency-aware (503 when degraded), which quietly turned this into a
    # flaky test: under connection contention a *cold* pool's first probe can
    # exceed the 5s health-probe timeout, and the assertion below fails with
    # 503 for reasons entirely unrelated to what it's testing. Observed
    # failing while a second app instance was competing for the same Postgres
    # and Redis, then passing on a quiet machine.
    resp = await client.get("/livez")
    assert resp.status_code == 200
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["cache-control"] == "no-store"
    assert "x-request-id" in resp.headers
    assert "x-correlation-id" in resp.headers


async def test_refresh_reuse_returns_401_over_http(client: httpx.AsyncClient, email_sender) -> None:
    await client.post(
        "/v1/auth/register", json={"email": "http-reuse@example.com", "password": "Correct-Horse-9!"}
    )
    await client.get("/v1/auth/verify-email", params={"token": email_sender.last_verification_token})
    login_resp = await client.post(
        "/v1/auth/login", json={"email": "http-reuse@example.com", "password": "Correct-Horse-9!"}
    )
    first_refresh = login_resp.json()["tokens"]["refresh_token"]

    rotate_resp = await client.post("/v1/auth/refresh", json={"refresh_token": first_refresh})
    assert rotate_resp.status_code == 200

    reuse_resp = await client.post("/v1/auth/refresh", json={"refresh_token": first_refresh})
    assert reuse_resp.status_code == 401
