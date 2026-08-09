"""HTTP-level tests for the identity/account and platform user-management
endpoints added for the admin console, plus regressions for two 500s that
reached the browser before they were caught.

Every test here goes through the real app, real Postgres and real crypto --
several of these failures are structurally unreachable from unit tests using
fakes (the exception-handler mapping in particular only exists at the API
layer).
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import text

from iam_platform.core.config import Settings
from iam_platform.infrastructure.db.session import build_engine_from_dsn
from scripts.bootstrap_tenant_catalog import seed_tenant_catalog

from .conftest import CapturingEmailSender

pytestmark = pytest.mark.integration

PASSWORD = "Correct-Horse-9!"


async def _register_and_login(
    client: httpx.AsyncClient, email_sender: CapturingEmailSender, email: str
) -> tuple[str, str]:
    """Returns (user_id, access_token) for a freshly verified account."""
    await client.post("/v1/auth/register", json={"email": email, "password": PASSWORD})
    await client.get(
        "/v1/auth/verify-email", params={"token": email_sender.last_verification_token}
    )
    resp = await client.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
    token = resp.json()["tokens"]["access_token"]

    me = await client.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    return me.json()["user_id"], token


async def _grant_platform_permissions(user_id: str, codes: list[str]) -> None:
    """Seeds a platform role holding `codes` and grants it, straight through the
    migrator connection.

    Deliberately bypasses the API: the self-escalation guard means no caller can
    mint the *first* platform permission through it (the same reason
    scripts/bootstrap_platform_admin.py exists), so a test that needs a
    privileged actor has to start one outside the guard.
    """
    settings = Settings()  # type: ignore[call-arg]
    engine = build_engine_from_dsn(settings.database.migrator_dsn, settings.database)
    async with engine.begin() as conn:
        role_id = uuid.uuid4()
        await conn.execute(
            text(
                "INSERT INTO platform_roles (id, code, name, is_system, rank) "
                "VALUES (:id, :code, 'Test Role', true, 900)"
            ),
            {"id": str(role_id), "code": f"test_role_{role_id.hex[:8]}"},
        )
        for code in codes:
            permission_id = uuid.uuid4()
            await conn.execute(
                text(
                    "INSERT INTO platform_permissions "
                    "(id, code, scope, resource, action, risk_level, is_system) "
                    "VALUES (:id, :code, 'platform', :res, :act, 'high', true) "
                    "ON CONFLICT (code) DO NOTHING"
                ),
                {
                    "id": str(permission_id),
                    "code": code,
                    "res": code.split(".")[1],
                    "act": code.split(".")[2],
                },
            )
            actual_id = (
                await conn.execute(
                    text("SELECT id FROM platform_permissions WHERE code = :c"), {"c": code}
                )
            ).scalar_one()
            await conn.execute(
                text(
                    "INSERT INTO platform_role_permissions (role_id, permission_id) "
                    "VALUES (:r, :p) ON CONFLICT DO NOTHING"
                ),
                {"r": str(role_id), "p": str(actual_id)},
            )
        await conn.execute(
            text(
                "INSERT INTO platform_user_roles (id, user_id, role_id, granted_by_user_id) "
                "VALUES (:id, :u, :r, :u)"
            ),
            {"id": str(uuid.uuid4()), "u": user_id, "r": str(role_id)},
        )
    await engine.dispose()


async def _seed_tenant_catalog() -> None:
    """Any test that creates a tenant through the API needs the 'tenant_owner'
    catalog role to exist first -- `CreateTenant` now refuses (503) rather
    than silently creating an ownerless tenant when it's missing, and the
    `client` fixture truncates every table (including `tenant_roles`) after
    each test, so this has to be re-seeded per test, not once for the suite.
    Reuses the same seeding function `scripts/bootstrap_tenant_catalog.py`
    uses, rather than a third copy of the catalog data.
    """
    settings = Settings()  # type: ignore[call-arg]
    engine = build_engine_from_dsn(settings.database.migrator_dsn, settings.database)
    async with engine.begin() as conn:
        await seed_tenant_catalog(conn)
    await engine.dispose()


# --- GET /v1/auth/me ---------------------------------------------------------


async def test_me_returns_profile_without_any_secret_material(
    client: httpx.AsyncClient, email_sender: CapturingEmailSender
) -> None:
    _, token = await _register_and_login(client, email_sender, "me-probe@example.com")

    resp = await client.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()

    assert body["email"] == "me-probe@example.com"
    assert body["status"] == "active"
    assert body["has_password"] is True
    assert body["mfa_methods"] == []
    assert body["linked_providers"] == []

    # The response shape must have no field capable of carrying a credential.
    serialized = resp.text.lower()
    for forbidden in ("password_hash", "argon2", "secret", "security_stamp"):
        assert forbidden not in serialized


async def test_me_requires_authentication(client: httpx.AsyncClient) -> None:
    assert (await client.get("/v1/auth/me")).status_code == 401


# --- POST /v1/auth/password/change -------------------------------------------


async def test_password_change_rotates_credential_and_kills_the_session(
    client: httpx.AsyncClient, email_sender: CapturingEmailSender
) -> None:
    _, token = await _register_and_login(client, email_sender, "pwchange@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post(
        "/v1/auth/password/change",
        json={"current_password": PASSWORD, "new_password": "Brand-New-Pass-7!"},
        headers=headers,
    )
    assert resp.status_code == 200

    # Old password no longer works, new one does.
    old = await client.post(
        "/v1/auth/login", json={"email": "pwchange@example.com", "password": PASSWORD}
    )
    assert old.status_code == 401
    new = await client.post(
        "/v1/auth/login",
        json={"email": "pwchange@example.com", "password": "Brand-New-Pass-7!"},
    )
    assert new.status_code == 200

    # The security stamp was bumped, so the token issued before the change is
    # dead -- that is the whole point of forcing a re-login.
    assert (await client.post("/v1/auth/logout-all", headers=headers)).status_code == 401


async def test_password_change_with_wrong_current_password_is_401_and_is_recorded(
    client: httpx.AsyncClient, email_sender: CapturingEmailSender
) -> None:
    """The 401 is the easy half. The important half is that the security event
    written just before it **survives the transaction**.

    `__aexit__` rolls back on any exception, so `raise`-ing from inside the
    `async with uow:` block that wrote the event would silently discard it --
    the trap documented in docs/18-schema-rls-and-migrations.md, which cost
    this project three real bugs in Phase 5. `ChangeMyPassword` therefore
    exits the block normally and raises afterwards; this test fails if anyone
    "simplifies" it back.
    """
    user_id, token = await _register_and_login(client, email_sender, "pwwrong@example.com")

    resp = await client.post(
        "/v1/auth/password/change",
        json={"current_password": "not-the-password", "new_password": "Brand-New-Pass-7!"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401

    # The original password must still work -- a failed attempt changes nothing.
    still_valid = await client.post(
        "/v1/auth/login", json={"email": "pwwrong@example.com", "password": PASSWORD}
    )
    assert still_valid.status_code == 200

    settings = Settings()  # type: ignore[call-arg]
    engine = build_engine_from_dsn(settings.database.migrator_dsn, settings.database)
    async with engine.begin() as conn:
        recorded = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM security_events "
                    "WHERE user_id = :u AND event_type = 'identity.password_change_failed'"
                ),
                {"u": user_id},
            )
        ).scalar_one()
    await engine.dispose()

    assert recorded == 1, "the failed-attempt security event was rolled back"


async def test_password_change_rejects_a_weak_new_password_with_violations(
    client: httpx.AsyncClient, email_sender: CapturingEmailSender
) -> None:
    _, token = await _register_and_login(client, email_sender, "pwweak@example.com")

    resp = await client.post(
        "/v1/auth/password/change",
        json={"current_password": PASSWORD, "new_password": "short"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422
    assert resp.json()["violations"]


# --- Platform user directory -------------------------------------------------


async def test_user_directory_requires_the_read_permission(
    client: httpx.AsyncClient, email_sender: CapturingEmailSender
) -> None:
    _, token = await _register_and_login(client, email_sender, "nodirectory@example.com")

    resp = await client.get("/v1/platform/users", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


async def test_user_directory_lists_and_searches(
    client: httpx.AsyncClient, email_sender: CapturingEmailSender
) -> None:
    admin_id, admin_token = await _register_and_login(
        client, email_sender, "dir-admin@example.com"
    )
    await _register_and_login(client, email_sender, "findme@example.com")
    await _grant_platform_permissions(admin_id, ["platform.users.read"])

    # A fresh token so the new role is reflected in the resolved permissions.
    login = await client.post(
        "/v1/auth/login", json={"email": "dir-admin@example.com", "password": PASSWORD}
    )
    headers = {"Authorization": f"Bearer {login.json()['tokens']['access_token']}"}

    listing = await client.get("/v1/platform/users", headers=headers)
    assert listing.status_code == 200
    assert listing.json()["total"] >= 2

    searched = await client.get("/v1/platform/users?search=findme", headers=headers)
    assert searched.status_code == 200
    emails = [u["email"] for u in searched.json()["users"]]
    assert emails == ["findme@example.com"]


async def test_suspending_an_account_blocks_that_users_next_request(
    client: httpx.AsyncClient, email_sender: CapturingEmailSender
) -> None:
    admin_id, _ = await _register_and_login(client, email_sender, "sus-admin@example.com")
    victim_id, victim_token = await _register_and_login(client, email_sender, "victim@example.com")
    await _grant_platform_permissions(
        admin_id, ["platform.users.read", "platform.users.manage"]
    )

    login = await client.post(
        "/v1/auth/login", json={"email": "sus-admin@example.com", "password": PASSWORD}
    )
    admin_headers = {"Authorization": f"Bearer {login.json()['tokens']['access_token']}"}
    victim_headers = {"Authorization": f"Bearer {victim_token}"}

    # Before: the victim's token works.
    assert (await client.get("/v1/auth/me", headers=victim_headers)).status_code == 200

    resp = await client.post(
        f"/v1/platform/users/{victim_id}/suspend",
        json={"reason": "testing"},
        headers=admin_headers,
    )
    assert resp.status_code == 204

    # After: suspension bumped the security stamp, so the already-issued access
    # token stops working immediately rather than at its natural expiry.
    assert (await client.get("/v1/auth/me", headers=victim_headers)).status_code == 401

    assert (
        await client.post(f"/v1/platform/users/{victim_id}/reactivate", headers=admin_headers)
    ).status_code == 204


async def test_an_admin_cannot_suspend_their_own_account(
    client: httpx.AsyncClient, email_sender: CapturingEmailSender
) -> None:
    """Locking the last administrator out is only recoverable with direct
    database access, so the use case refuses it outright."""
    admin_id, _ = await _register_and_login(client, email_sender, "selfsus@example.com")
    await _grant_platform_permissions(
        admin_id, ["platform.users.read", "platform.users.manage"]
    )
    login = await client.post(
        "/v1/auth/login", json={"email": "selfsus@example.com", "password": PASSWORD}
    )
    headers = {"Authorization": f"Bearer {login.json()['tokens']['access_token']}"}

    resp = await client.post(
        f"/v1/platform/users/{admin_id}/suspend", json={"reason": "oops"}, headers=headers
    )
    assert resp.status_code == 403


# --- Tenant lifecycle regressions --------------------------------------------


async def test_tenant_suspend_without_permission_is_403_not_500(
    client: httpx.AsyncClient, email_sender: CapturingEmailSender
) -> None:
    """Regression: `TenantCreationDeniedError` and `DuplicateSlugError` were
    declared as bare `Exception` subclasses in manage_tenants.py rather than
    under `PlatformAuthzError`, so no handler in api/exception_handlers.py
    matched them and a permission-denied tenant action surfaced as a 500.
    """
    _, token = await _register_and_login(client, email_sender, "notenant@example.com")

    resp = await client.post(
        f"/v1/platform/tenants/{uuid.uuid4()}/suspend",
        json={"reason": "nope"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


async def test_tenant_suspend_then_reactivate_round_trips(
    client: httpx.AsyncClient, email_sender: CapturingEmailSender
) -> None:
    """`Tenant.activate()` existed in the domain from Phase 6 but had no use
    case or route, so a suspended tenant could not be brought back through the
    API at all."""
    admin_id, _ = await _register_and_login(client, email_sender, "tenant-admin@example.com")
    await _grant_platform_permissions(
        admin_id, ["platform.tenants.create", "platform.tenants.suspend"]
    )
    await _seed_tenant_catalog()
    login = await client.post(
        "/v1/auth/login", json={"email": "tenant-admin@example.com", "password": PASSWORD}
    )
    headers = {"Authorization": f"Bearer {login.json()['tokens']['access_token']}"}

    created = await client.post(
        "/v1/platform/tenants",
        json={"slug": "roundtrip", "display_name": "Round Trip", "owner_user_id": admin_id},
        headers=headers,
    )
    assert created.status_code == 201
    tenant_id = created.json()["tenant_id"]

    # A second tenant on the same slug is a conflict, not a server error.
    duplicate = await client.post(
        "/v1/platform/tenants",
        json={"slug": "roundtrip", "display_name": "Dupe", "owner_user_id": admin_id},
        headers=headers,
    )
    assert duplicate.status_code == 409

    assert (
        await client.post(
            f"/v1/platform/tenants/{tenant_id}/suspend",
            json={"reason": "testing"},
            headers=headers,
        )
    ).status_code == 204

    tenants = await client.get("/v1/platform/tenants", headers=headers)
    assert next(t for t in tenants.json() if t["id"] == tenant_id)["status"] == "suspended"

    assert (
        await client.post(f"/v1/platform/tenants/{tenant_id}/reactivate", headers=headers)
    ).status_code == 204

    tenants = await client.get("/v1/platform/tenants", headers=headers)
    assert next(t for t in tenants.json() if t["id"] == tenant_id)["status"] == "active"


async def test_suspending_an_unknown_tenant_is_404_not_400(
    client: httpx.AsyncClient, email_sender: CapturingEmailSender
) -> None:
    admin_id, _ = await _register_and_login(client, email_sender, "notfound@example.com")
    await _grant_platform_permissions(admin_id, ["platform.tenants.suspend"])
    login = await client.post(
        "/v1/auth/login", json={"email": "notfound@example.com", "password": PASSWORD}
    )
    headers = {"Authorization": f"Bearer {login.json()['tokens']['access_token']}"}

    resp = await client.post(
        f"/v1/platform/tenants/{uuid.uuid4()}/suspend",
        json={"reason": "nope"},
        headers=headers,
    )
    assert resp.status_code == 404


# --- Role -> permission mapping ----------------------------------------------


# --- Administrator-driven user lifecycle -------------------------------------


async def test_admin_can_create_a_user_who_can_then_sign_in(
    client: httpx.AsyncClient, email_sender: CapturingEmailSender
) -> None:
    admin_id, _ = await _register_and_login(client, email_sender, "creator@example.com")
    await _grant_platform_permissions(
        admin_id, ["platform.users.read", "platform.users.manage"]
    )
    login = await client.post(
        "/v1/auth/login", json={"email": "creator@example.com", "password": PASSWORD}
    )
    headers = {"Authorization": f"Bearer {login.json()['tokens']['access_token']}"}

    created = await client.post(
        "/v1/platform/users",
        json={"email": "provisioned@example.com", "password": PASSWORD},
        headers=headers,
    )
    assert created.status_code == 201

    # Active immediately -- an administrator creating the account is the
    # vouching step email verification would otherwise provide, and this
    # deployment can't deliver mail at all.
    signed_in = await client.post(
        "/v1/auth/login", json={"email": "provisioned@example.com", "password": PASSWORD}
    )
    assert signed_in.status_code == 200

    duplicate = await client.post(
        "/v1/platform/users",
        json={"email": "provisioned@example.com", "password": PASSWORD},
        headers=headers,
    )
    assert duplicate.status_code == 409

    weak = await client.post(
        "/v1/platform/users",
        json={"email": "weakling@example.com", "password": "short"},
        headers=headers,
    )
    assert weak.status_code == 422
    assert weak.json()["violations"]


async def test_admin_can_rename_and_soft_delete_a_user(
    client: httpx.AsyncClient, email_sender: CapturingEmailSender
) -> None:
    admin_id, _ = await _register_and_login(client, email_sender, "lifecycle@example.com")
    target_id, _ = await _register_and_login(client, email_sender, "before@example.com")
    await _grant_platform_permissions(
        admin_id, ["platform.users.read", "platform.users.manage"]
    )
    login = await client.post(
        "/v1/auth/login", json={"email": "lifecycle@example.com", "password": PASSWORD}
    )
    headers = {"Authorization": f"Bearer {login.json()['tokens']['access_token']}"}

    renamed = await client.patch(
        f"/v1/platform/users/{target_id}", json={"email": "after@example.com"}, headers=headers
    )
    assert renamed.status_code == 204

    # The new address works, the old one is gone.
    assert (
        await client.post(
            "/v1/auth/login", json={"email": "after@example.com", "password": PASSWORD}
        )
    ).status_code == 200
    assert (
        await client.post(
            "/v1/auth/login", json={"email": "before@example.com", "password": PASSWORD}
        )
    ).status_code == 401

    # Renaming onto a taken address is a conflict, not a silent no-op.
    assert (
        await client.patch(
            f"/v1/platform/users/{target_id}",
            json={"email": "lifecycle@example.com"},
            headers=headers,
        )
    ).status_code == 409

    assert (
        await client.delete(f"/v1/platform/users/{target_id}", headers=headers)
    ).status_code == 204

    listing = await client.get("/v1/platform/users?search=after", headers=headers)
    assert listing.json()["total"] == 0

    assert (
        await client.delete(f"/v1/platform/users/{admin_id}", headers=headers)
    ).status_code == 403


async def test_a_suspended_account_cannot_sign_in_again(
    client: httpx.AsyncClient, email_sender: CapturingEmailSender
) -> None:
    """The bug this guards was severe: suspension bumped the security stamp,
    which killed *already-issued* tokens, but `LoginUser` never looked at the
    user's status — so the suspended person could simply sign in again and be
    handed fresh ones. `platform.users.manage` was, in effect, cosmetic.

    Deleting had the same hole. Both directions are asserted here, along with
    reactivation restoring access, because a fix that over-corrects (locking
    out reactivated accounts) is just as broken.
    """
    admin_id, _ = await _register_and_login(client, email_sender, "gatekeeper@example.com")
    target_id, _ = await _register_and_login(client, email_sender, "gated@example.com")
    await _grant_platform_permissions(
        admin_id, ["platform.users.read", "platform.users.manage"]
    )
    login = await client.post(
        "/v1/auth/login", json={"email": "gatekeeper@example.com", "password": PASSWORD}
    )
    headers = {"Authorization": f"Bearer {login.json()['tokens']['access_token']}"}

    creds = {"email": "gated@example.com", "password": PASSWORD}
    assert (await client.post("/v1/auth/login", json=creds)).status_code == 200

    await client.post(
        f"/v1/platform/users/{target_id}/suspend", json={"reason": "testing"}, headers=headers
    )
    assert (await client.post("/v1/auth/login", json=creds)).status_code == 401

    await client.post(f"/v1/platform/users/{target_id}/reactivate", headers=headers)
    assert (await client.post("/v1/auth/login", json=creds)).status_code == 200

    await client.delete(f"/v1/platform/users/{target_id}", headers=headers)
    assert (await client.post("/v1/auth/login", json=creds)).status_code == 401


async def test_creating_a_second_tenant_for_the_same_owner_succeeds(
    client: httpx.AsyncClient, email_sender: CapturingEmailSender
) -> None:
    """`ux_tenant_memberships_one_default_per_user` is a partial unique index
    over `user_id WHERE is_default`, and `CreateTenant` hard-coded
    `is_default=True` on the owner's membership. The second tenant for any
    owner therefore raised UniqueViolationError -- a 500 on a completely
    ordinary action. Only the first membership may claim the default slot.
    """
    admin_id, _ = await _register_and_login(client, email_sender, "multi@example.com")
    await _grant_platform_permissions(admin_id, ["platform.tenants.create"])
    await _seed_tenant_catalog()
    login = await client.post(
        "/v1/auth/login", json={"email": "multi@example.com", "password": PASSWORD}
    )
    headers = {"Authorization": f"Bearer {login.json()['tokens']['access_token']}"}

    for slug in ("first-org", "second-org"):
        resp = await client.post(
            "/v1/platform/tenants",
            json={"slug": slug, "display_name": slug, "owner_user_id": admin_id},
            headers=headers,
        )
        assert resp.status_code == 201, f"creating {slug} failed: {resp.text}"

    memberships = await client.get("/v1/tenants/me/memberships", headers=headers)
    defaults = [m for m in memberships.json() if m["is_default"]]
    assert len(defaults) == 1, "exactly one membership may be the default"


async def test_platform_role_permission_map_reports_what_each_role_grants(
    client: httpx.AsyncClient, email_sender: CapturingEmailSender
) -> None:
    admin_id, _ = await _register_and_login(client, email_sender, "rolemap@example.com")
    await _grant_platform_permissions(
        admin_id, ["platform.tenants.create", "platform.tenants.suspend"]
    )
    login = await client.post(
        "/v1/auth/login", json={"email": "rolemap@example.com", "password": PASSWORD}
    )
    headers = {"Authorization": f"Bearer {login.json()['tokens']['access_token']}"}

    resp = await client.get("/v1/platform/roles/permissions", headers=headers)
    assert resp.status_code == 200

    mapping = resp.json()["by_role_code"]
    granted = {code for codes in mapping.values() for code in codes}
    assert {"platform.tenants.create", "platform.tenants.suspend"} <= granted
