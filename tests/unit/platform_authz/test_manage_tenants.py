"""CreateTenant/SuspendTenant -- platform-permission-gated tenant lifecycle,
run on the BYPASSRLS platform connection (docs/01 responsibility matrix).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from iam_platform.application.platform_authz.list_tenants import (
    ListTenants,
    ListTenantsQuery,
    TenantListDeniedError,
)
from iam_platform.application.platform_authz.manage_tenants import (
    CreateTenant,
    CreateTenantCommand,
    DuplicateSlugError,
    SuspendTenant,
    SuspendTenantCommand,
    TenantCreationDeniedError,
    TenantOwnerRoleNotSeededError,
)
from iam_platform.core.clock import FixedClock
from iam_platform.domain.platform_authz.entities import PlatformUserRole
from iam_platform.domain.tenancy.entities import Tenant, TenantStatus
from tests.unit.tenant_authz.fakes import (
    FakePlatformUnitOfWork,
    make_platform_role,
    make_tenant_role,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _grant_actor_permission(uow: FakePlatformUnitOfWork, actor_id, code: str) -> None:
    role = make_platform_role(code="tenant_creator", rank=50, now=NOW)
    uow.platform_roles.by_id[role.id] = role
    uow.platform_permissions.role_permission_codes[role.id] = {code}
    uow.platform_user_roles.by_id[uuid4()] = PlatformUserRole(
        id=uuid4(), user_id=actor_id, role_id=role.id, granted_by_user_id=actor_id, granted_at=NOW
    )


class TestCreateTenant:
    async def test_creates_tenant_and_bootstraps_owner_membership_and_role(self) -> None:
        uow = FakePlatformUnitOfWork()
        actor_id, owner_id = uuid4(), uuid4()
        _grant_actor_permission(uow, actor_id, "platform.tenants.create")
        owner_role = make_tenant_role(tenant_id=None, code="tenant_owner", rank=100, now=NOW, is_system=True)
        uow.tenant_roles.by_id[owner_role.id] = owner_role

        use_case = CreateTenant(uow, FixedClock(NOW))
        tenant_id = await use_case.execute(
            CreateTenantCommand(
                actor_user_id=str(actor_id),
                slug="acme",
                display_name="Acme Corp",
                owner_user_id=str(owner_id),
            )
        )

        tenant = await uow.tenants.get_by_id(tenant_id)
        assert tenant is not None
        assert tenant.status == TenantStatus.ACTIVE

        membership = await uow.tenant_memberships.get_by_tenant_and_user(tenant_id, owner_id)
        assert membership is not None
        assert membership.is_default is True

        assignment = await uow.tenant_membership_roles.get_active(
            membership_id=membership.id, role_id=owner_role.id
        )
        assert assignment is not None
        assert uow.audit.events[0]["action"] == "tenancy.tenant_created"

    async def test_denied_without_the_create_permission(self) -> None:
        uow = FakePlatformUnitOfWork()
        actor_id, owner_id = uuid4(), uuid4()

        use_case = CreateTenant(uow, FixedClock(NOW))
        with pytest.raises(TenantCreationDeniedError):
            await use_case.execute(
                CreateTenantCommand(
                    actor_user_id=str(actor_id),
                    slug="acme",
                    display_name="Acme Corp",
                    owner_user_id=str(owner_id),
                )
            )
        assert uow.tenants.by_id == {}

    async def test_duplicate_slug_rejected(self) -> None:
        uow = FakePlatformUnitOfWork()
        actor_id, owner_id = uuid4(), uuid4()
        _grant_actor_permission(uow, actor_id, "platform.tenants.create")
        existing = Tenant(
            id=uuid4(),
            slug="acme",
            display_name="Existing",
            owner_user_id=owner_id,
            created_at=NOW,
            updated_at=NOW,
        )
        uow.tenants.by_id[existing.id] = existing

        use_case = CreateTenant(uow, FixedClock(NOW))
        with pytest.raises(DuplicateSlugError):
            await use_case.execute(
                CreateTenantCommand(
                    actor_user_id=str(actor_id),
                    slug="acme",
                    display_name="Acme Corp",
                    owner_user_id=str(owner_id),
                )
            )
        assert len(uow.tenants.by_id) == 1  # no second tenant created

    async def test_raises_when_owner_role_catalog_is_not_seeded(self) -> None:
        """Regression test: a fresh deployment that never ran
        scripts/bootstrap_tenant_catalog.py used to get a tenant created
        successfully with an owner who held zero permissions in it, because
        the missing 'tenant_owner' catalog role was looked up and silently
        skipped rather than treated as an error."""
        uow = FakePlatformUnitOfWork()
        actor_id, owner_id = uuid4(), uuid4()
        _grant_actor_permission(uow, actor_id, "platform.tenants.create")
        # Deliberately no tenant_owner role seeded into uow.tenant_roles.

        use_case = CreateTenant(uow, FixedClock(NOW))
        with pytest.raises(TenantOwnerRoleNotSeededError):
            await use_case.execute(
                CreateTenantCommand(
                    actor_user_id=str(actor_id),
                    slug="acme",
                    display_name="Acme Corp",
                    owner_user_id=str(owner_id),
                )
            )
        # Rolled back entirely -- no half-created tenant with a
        # permission-less owner left behind.
        assert uow.tenants.by_id == {}
        assert uow.tenant_memberships.by_id == {}


class TestSuspendTenant:
    async def test_suspends_and_audits(self) -> None:
        uow = FakePlatformUnitOfWork()
        actor_id, owner_id = uuid4(), uuid4()
        _grant_actor_permission(uow, actor_id, "platform.tenants.suspend")
        tenant = Tenant(
            id=uuid4(),
            slug="acme",
            display_name="Acme",
            status=TenantStatus.ACTIVE,
            owner_user_id=owner_id,
            created_at=NOW,
            updated_at=NOW,
        )
        uow.tenants.by_id[tenant.id] = tenant

        use_case = SuspendTenant(uow, FixedClock(NOW))
        await use_case.execute(
            SuspendTenantCommand(actor_user_id=str(actor_id), tenant_id=str(tenant.id), reason="abuse")
        )

        assert tenant.status == TenantStatus.SUSPENDED
        assert uow.audit.events[0]["action"] == "tenancy.tenant_suspended"

    async def test_denied_without_the_suspend_permission(self) -> None:
        uow = FakePlatformUnitOfWork()
        actor_id, owner_id = uuid4(), uuid4()
        tenant = Tenant(
            id=uuid4(),
            slug="acme",
            display_name="Acme",
            status=TenantStatus.ACTIVE,
            owner_user_id=owner_id,
            created_at=NOW,
            updated_at=NOW,
        )
        uow.tenants.by_id[tenant.id] = tenant

        use_case = SuspendTenant(uow, FixedClock(NOW))
        with pytest.raises(TenantCreationDeniedError):
            await use_case.execute(
                SuspendTenantCommand(actor_user_id=str(actor_id), tenant_id=str(tenant.id), reason="abuse")
            )
        assert tenant.status == TenantStatus.ACTIVE


class TestListTenants:
    async def test_actor_with_permission_sees_every_tenant(self) -> None:
        uow = FakePlatformUnitOfWork()
        actor_id = uuid4()
        _grant_actor_permission(uow, actor_id, "platform.tenants.create")
        for slug in ("acme", "globex"):
            tenant = Tenant(
                id=uuid4(),
                slug=slug,
                display_name=slug.title(),
                status=TenantStatus.ACTIVE,
                owner_user_id=uuid4(),
                created_at=NOW,
                updated_at=NOW,
            )
            uow.tenants.by_id[tenant.id] = tenant

        use_case = ListTenants(uow, FixedClock(NOW))
        tenants = await use_case.execute(ListTenantsQuery(actor_user_id=str(actor_id)))

        assert {t.slug for t in tenants} == {"acme", "globex"}

    async def test_denied_without_permission(self) -> None:
        uow = FakePlatformUnitOfWork()
        uow.tenants.by_id[uuid4()] = Tenant(
            id=uuid4(),
            slug="acme",
            display_name="Acme",
            status=TenantStatus.ACTIVE,
            owner_user_id=uuid4(),
            created_at=NOW,
            updated_at=NOW,
        )

        use_case = ListTenants(uow, FixedClock(NOW))
        with pytest.raises(TenantListDeniedError):
            await use_case.execute(ListTenantsQuery(actor_user_id=str(uuid4())))
