"""HTTP-level authorization tests for the AI-resource endpoints.

These exist because the application-layer tests can't prove the *dependency
chain* is wired: a use case that correctly rejects an under-permissioned
caller is useless if the route never resolves permissions, and a route that
forgets ``Depends(get_effective_tenant_permissions)`` would still pass every
unit test. Everything here goes through the real app, real Postgres, and real
JWT verification.
"""

from __future__ import annotations

from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text

from iam_platform.core.config import Settings
from iam_platform.infrastructure.db.session import build_engine_from_dsn

from .conftest import CapturingEmailSender

pytestmark = pytest.mark.integration


async def _register_and_login(
    client: httpx.AsyncClient, email_sender: CapturingEmailSender, email: str
) -> tuple[str, str]:
    """Returns (user_id, access_token) for a verified, logged-in user.

    Takes the sender as a parameter rather than digging it out of the client's
    transport -- the conftest already exposes it as a fixture, and the same
    instance is wired into the app.
    """
    resp = await client.post(
        "/v1/auth/register", json={"email": email, "password": "Correct-Horse-9!"}
    )
    assert resp.status_code == 202

    token = email_sender.last_verification_token
    assert token is not None

    resp = await client.get("/v1/auth/verify-email", params={"token": token})
    assert resp.status_code == 200

    resp = await client.post(
        "/v1/auth/login", json={"email": email, "password": "Correct-Horse-9!"}
    )
    assert resp.status_code == 200
    body = resp.json()
    access_token = body["tokens"]["access_token"]

    async with _migrator() as conn:
        result = await conn.execute(
            text("SELECT id FROM users WHERE email = :e"), {"e": email}
        )
        user_id = str(result.scalar_one())
    return user_id, access_token


def _migrator():
    settings = Settings()  # type: ignore[call-arg]
    engine = build_engine_from_dsn(settings.database.migrator_dsn, settings.database)
    return engine.begin()


async def _seed_tenant_with_member(user_id: str, *, permissions: list[str]) -> str:
    """Creates a tenant, an active membership for ``user_id``, and a role
    carrying ``permissions``. Returns the tenant id."""
    tenant_id, membership_id, role_id = uuid4(), uuid4(), uuid4()

    async with _migrator() as conn:
        await conn.execute(
            text(
                "INSERT INTO tenants (id, slug, display_name, status, owner_user_id) "
                "VALUES (:id, :slug, 'T', 'active', :owner)"
            ),
            {"id": str(tenant_id), "slug": f"api-{tenant_id}", "owner": user_id},
        )
        await conn.execute(
            text(
                "INSERT INTO tenant_memberships "
                "(id, tenant_id, user_id, status, is_default, metadata, created_at, updated_at) "
                "VALUES (:id, :tid, :uid, 'active', false, '{}'::jsonb, now(), now())"
            ),
            {"id": str(membership_id), "tid": str(tenant_id), "uid": user_id},
        )
        await conn.execute(
            text(
                "INSERT INTO tenant_roles (id, tenant_id, code, name, is_system, rank) "
                "VALUES (:id, :tid, :code, 'Role', false, 10)"
            ),
            {"id": str(role_id), "tid": str(tenant_id), "code": f"role-{role_id}"},
        )
        for code in permissions:
            permission_id = uuid4()
            await conn.execute(
                text(
                    "INSERT INTO tenant_permissions "
                    "(id, code, resource, action, risk_level, is_system, tenant_customizable) "
                    "VALUES (:id, :code, 'r', 'a', 'low', true, true) "
                    "ON CONFLICT (code) DO NOTHING"
                ),
                {"id": str(permission_id), "code": code},
            )
            resolved = await conn.execute(
                text("SELECT id FROM tenant_permissions WHERE code = :code"), {"code": code}
            )
            await conn.execute(
                text(
                    "INSERT INTO tenant_role_permissions (role_id, permission_id, tenant_id) "
                    "VALUES (:r, :p, :tid)"
                ),
                {"r": str(role_id), "p": str(resolved.scalar_one()), "tid": str(tenant_id)},
            )
        await conn.execute(
            text(
                "INSERT INTO tenant_membership_roles "
                "(id, tenant_id, membership_id, role_id, granted_by_user_id) "
                "VALUES (:id, :tid, :mid, :rid, :uid)"
            ),
            {
                "id": str(uuid4()),
                "tid": str(tenant_id),
                "mid": str(membership_id),
                "rid": str(role_id),
                "uid": user_id,
            },
        )
    return str(tenant_id)


class TestAuthenticationIsRequired:
    async def test_unauthenticated_request_is_rejected(self, client: httpx.AsyncClient) -> None:
        resp = await client.get(f"/v1/tenants/{uuid4()}/assistants")
        assert resp.status_code in (401, 403)

    async def test_garbage_bearer_token_is_rejected(self, client: httpx.AsyncClient) -> None:
        resp = await client.get(
            f"/v1/tenants/{uuid4()}/assistants",
            headers={"Authorization": "Bearer not-a-real-jwt"},
        )
        assert resp.status_code == 401


class TestTenantScopingAtTheRouteBoundary:
    async def test_authenticated_non_member_gets_404_not_403(
        self, client: httpx.AsyncClient, email_sender: CapturingEmailSender
    ) -> None:
        """Scenario 1/2 at the HTTP boundary: a valid token for a user with no
        membership in the requested tenant must not distinguish "tenant does
        not exist" from "you are not in it"."""
        _, access_token = await _register_and_login(client, email_sender, "outsider@example.com")
        stranger_tenant_id = uuid4()

        resp = await client.get(
            f"/v1/tenants/{stranger_tenant_id}/assistants",
            headers={
                "Authorization": f"Bearer {access_token}",
                "X-Tenant-Id": str(stranger_tenant_id),
            },
        )
        assert resp.status_code == 404

    async def test_member_of_another_tenant_cannot_reach_this_one(
        self, client: httpx.AsyncClient, email_sender: CapturingEmailSender
    ) -> None:
        user_id, access_token = await _register_and_login(client, email_sender, "tenant-a@example.com")
        await _seed_tenant_with_member(user_id, permissions=["tenant.assistants.create"])
        someone_elses_tenant = uuid4()

        resp = await client.get(
            f"/v1/tenants/{someone_elses_tenant}/assistants",
            headers={
                "Authorization": f"Bearer {access_token}",
                "X-Tenant-Id": str(someone_elses_tenant),
            },
        )
        assert resp.status_code == 404


class TestPermissionEnforcementAtTheRouteBoundary:
    async def test_member_without_the_permission_is_forbidden(
        self, client: httpx.AsyncClient, email_sender: CapturingEmailSender
    ) -> None:
        """The route resolves permissions and the use case rejects -- proving
        the chain is wired, not just that the use case works in isolation."""
        user_id, access_token = await _register_and_login(client, email_sender, "reader@example.com")
        tenant_id = await _seed_tenant_with_member(
            user_id, permissions=["tenant.resources.read"]  # NOT assistants.create
        )

        resp = await client.post(
            f"/v1/tenants/{tenant_id}/assistants",
            headers={
                "Authorization": f"Bearer {access_token}",
                "X-Tenant-Id": tenant_id,
            },
            json={
                "name": "Unauthorized Bot",
                "model_configuration_id": str(uuid4()),
            },
        )
        assert resp.status_code == 403

    async def test_member_with_the_permission_gets_past_authorization(
        self, client: httpx.AsyncClient, email_sender: CapturingEmailSender
    ) -> None:
        """Guards against the previous test passing because *everything* 403s.

        The request still fails -- the model configuration doesn't exist -- but
        with 404, proving it cleared the permission gate and reached the use
        case's own validation.
        """
        user_id, access_token = await _register_and_login(client, email_sender, "creator@example.com")
        tenant_id = await _seed_tenant_with_member(
            user_id, permissions=["tenant.assistants.create"]
        )

        resp = await client.post(
            f"/v1/tenants/{tenant_id}/assistants",
            headers={
                "Authorization": f"Bearer {access_token}",
                "X-Tenant-Id": tenant_id,
            },
            json={
                "name": "Authorized Bot",
                "model_configuration_id": str(uuid4()),  # nonexistent
            },
        )
        assert resp.status_code == 404
        assert resp.status_code != 403  # explicitly: not a permission failure

    async def test_provider_credential_listing_requires_manage_permission(
        self, client: httpx.AsyncClient, email_sender: CapturingEmailSender
    ) -> None:
        user_id, access_token = await _register_and_login(client, email_sender, "nosecrets@example.com")
        tenant_id = await _seed_tenant_with_member(
            user_id, permissions=["tenant.resources.read"]
        )

        resp = await client.get(
            f"/v1/tenants/{tenant_id}/provider-credentials",
            headers={
                "Authorization": f"Bearer {access_token}",
                "X-Tenant-Id": tenant_id,
            },
        )
        assert resp.status_code == 403


class TestSecretsNeverAppearInResponses:
    async def test_stored_credential_response_omits_the_secret(
        self, client: httpx.AsyncClient, email_sender: CapturingEmailSender
    ) -> None:
        user_id, access_token = await _register_and_login(client, email_sender, "keeper@example.com")
        tenant_id = await _seed_tenant_with_member(
            user_id, permissions=["tenant.provider_credentials.manage"]
        )
        secret = "sk-do-not-leak-me-1234"

        resp = await client.post(
            f"/v1/tenants/{tenant_id}/provider-credentials",
            headers={
                "Authorization": f"Bearer {access_token}",
                "X-Tenant-Id": tenant_id,
            },
            json={"provider": "anthropic", "secret": secret},
        )
        assert resp.status_code == 201
        assert secret not in resp.text
        body = resp.json()
        assert body["key_hint"] == "1234"
        assert "credential_ciphertext" not in body

        # ...and not on the way back out either.
        resp = await client.get(
            f"/v1/tenants/{tenant_id}/provider-credentials",
            headers={
                "Authorization": f"Bearer {access_token}",
                "X-Tenant-Id": tenant_id,
            },
        )
        assert resp.status_code == 200
        assert secret not in resp.text
