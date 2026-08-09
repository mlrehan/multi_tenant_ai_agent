"""End-to-end proof that the Phase 6 application layer works together against
real Postgres (not fakes): CreateTenant -> InviteMember -> AcceptInvitation ->
AssignMembershipRole -> ResolveTenantEffectivePermissions.

No seed data ships in the baseline migration (deliberately -- catalog rows
are an ops/fixture concern, not schema), so this test seeds its own minimal
role/permission catalog via the migrator (table-owner) connection, exactly
the way a real deployment's seed script would, before driving everything
else through the real application layer and its Postgres-backed
repositories/UnitsOfWork.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from iam_platform.application.platform_authz.manage_tenants import CreateTenant, CreateTenantCommand
from iam_platform.application.tenancy.invite_member import (
    AcceptInvitation,
    AcceptInvitationCommand,
    InviteMember,
    InviteMemberCommand,
)
from iam_platform.application.tenant_authz.assign_membership_role import (
    AssignMembershipRole,
    AssignMembershipRoleCommand,
)
from iam_platform.application.tenant_authz.effective_permissions import (
    ResolveTenantEffectivePermissions,
    ResolveTenantEffectivePermissionsQuery,
)
from iam_platform.core.clock import SystemClock
from tests.unit.tenant_authz.fakes import FakeInvitationEmailSender

pytestmark = pytest.mark.integration


async def _seed_catalog(migrator_engine: AsyncEngine) -> dict[str, object]:
    admin_user_id, invitee_user_id = uuid4(), uuid4()
    platform_role_id, tenant_owner_role_id, member_role_id = uuid4(), uuid4(), uuid4()
    create_tenant_perm_id, invite_perm_id, manage_roles_perm_id, read_perm_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )

    async with migrator_engine.begin() as conn:
        for uid, email in ((admin_user_id, "admin"), (invitee_user_id, "invitee")):
            await conn.execute(
                text(
                    "INSERT INTO users (id, email, status, security_stamp) "
                    "VALUES (:id, :email, 'active', :ss)"
                ),
                {"id": str(uid), "email": f"{email}-{uid}@example.com", "ss": str(uuid4())},
            )

        await conn.execute(
            text(
                "INSERT INTO platform_roles (id, code, name, is_system, rank) "
                "VALUES (:id, 'platform_admin', 'Platform Admin', true, 100)"
            ),
            {"id": str(platform_role_id)},
        )
        await conn.execute(
            text(
                "INSERT INTO platform_permissions (id, code, scope, resource, action, is_system, risk_level) "
                "VALUES (:id, 'platform.tenants.create', 'platform', 'tenants', 'create', true, 'high')"
            ),
            {"id": str(create_tenant_perm_id)},
        )
        await conn.execute(
            text(
                "INSERT INTO platform_role_permissions (role_id, permission_id) VALUES (:r, :p)"
            ),
            {"r": str(platform_role_id), "p": str(create_tenant_perm_id)},
        )
        await conn.execute(
            text(
                "INSERT INTO platform_user_roles (id, user_id, role_id, granted_by_user_id) "
                "VALUES (:id, :u, :r, :u)"
            ),
            {"id": str(uuid4()), "u": str(admin_user_id), "r": str(platform_role_id)},
        )

        await conn.execute(
            text(
                "INSERT INTO tenant_roles (id, tenant_id, code, name, is_system, rank) "
                "VALUES (:id, NULL, 'tenant_owner', 'Tenant Owner', true, 1000)"
            ),
            {"id": str(tenant_owner_role_id)},
        )
        await conn.execute(
            text(
                "INSERT INTO tenant_roles (id, tenant_id, code, name, is_system, rank) "
                "VALUES (:id, NULL, 'member', 'Member', true, 10)"
            ),
            {"id": str(member_role_id)},
        )
        for perm_id, code in (
            (invite_perm_id, "tenant.users.invite"),
            (manage_roles_perm_id, "tenant.roles.manage"),
            (read_perm_id, "tenant.resources.read"),
        ):
            await conn.execute(
                text(
                    "INSERT INTO tenant_permissions "
                    "(id, code, resource, action, risk_level, is_system, tenant_customizable) "
                    "VALUES (:id, :code, 'tenant', 'act', 'low', true, true)"
                ),
                {"id": str(perm_id), "code": code},
            )
        for perm_id in (invite_perm_id, manage_roles_perm_id, read_perm_id):
            await conn.execute(
                text(
                    "INSERT INTO tenant_role_permissions (role_id, permission_id, tenant_id) "
                    "VALUES (:r, :p, NULL)"
                ),
                {"r": str(tenant_owner_role_id), "p": str(perm_id)},
            )
        await conn.execute(
            text(
                "INSERT INTO tenant_role_permissions (role_id, permission_id, tenant_id) "
                "VALUES (:r, :p, NULL)"
            ),
            {"r": str(member_role_id), "p": str(read_perm_id)},
        )

    return {"admin_user_id": admin_user_id, "invitee_user_id": invitee_user_id}


class TestPlatformAndTenantAuthzFlow:
    async def test_create_tenant_invite_accept_assign_role_resolve_permissions(
        self, tenant_uow_factory, platform_uow_factory, migrator_engine: AsyncEngine
    ) -> None:
        seeded = await _seed_catalog(migrator_engine)
        admin_user_id = seeded["admin_user_id"]
        invitee_user_id = seeded["invitee_user_id"]
        clock = SystemClock()

        tenant_id = await CreateTenant(platform_uow_factory, clock).execute(
            CreateTenantCommand(
                actor_user_id=str(admin_user_id),
                slug=f"acme-{uuid4().hex[:8]}",
                display_name="Acme Corp",
                owner_user_id=str(admin_user_id),
            )
        )

        email_sender = FakeInvitationEmailSender()
        await InviteMember(tenant_uow_factory, email_sender, clock).execute(
            InviteMemberCommand(
                actor_user_id=str(admin_user_id),
                tenant_id=str(tenant_id),
                email="new.member@example.com",
                role_codes=[],
            )
        )
        assert len(email_sender.sent) == 1
        _, raw_token, _ = email_sender.sent[0]

        membership_id = await AcceptInvitation(tenant_uow_factory, clock).execute(
            AcceptInvitationCommand(
                accepting_user_id=str(invitee_user_id),
                accepting_user_email="new.member@example.com",
                tenant_id=str(tenant_id),
                token=raw_token,
            )
        )

        await AssignMembershipRole(tenant_uow_factory, clock).execute(
            AssignMembershipRoleCommand(
                actor_user_id=str(admin_user_id),
                tenant_id=str(tenant_id),
                target_membership_id=str(membership_id),
                role_code="member",
            )
        )

        permissions = await ResolveTenantEffectivePermissions(tenant_uow_factory, clock).execute(
            ResolveTenantEffectivePermissionsQuery(
                tenant_id=str(tenant_id), user_id=str(invitee_user_id)
            )
        )
        assert permissions == frozenset({"tenant.resources.read"})

        owner_permissions = await ResolveTenantEffectivePermissions(tenant_uow_factory, clock).execute(
            ResolveTenantEffectivePermissionsQuery(tenant_id=str(tenant_id), user_id=str(admin_user_id))
        )
        assert "tenant.users.invite" in owner_permissions
        assert "tenant.roles.manage" in owner_permissions
