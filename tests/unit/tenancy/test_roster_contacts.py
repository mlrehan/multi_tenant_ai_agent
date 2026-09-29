"""The member roster names people, behind the roster's own permission.

The roster used to list user ids only, so an administrator could not tell
whom they were about to suspend. It now carries each member's email and
display name -- from a projection scoped to this tenant -- and only for a
caller who may manage members in the first place.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from iam_platform.application.tenancy.exceptions import PermissionDeniedError
from iam_platform.application.tenancy.list_tenant_members import (
    ListTenantMembers,
    ListTenantMembersQuery,
)
from iam_platform.application.tenancy.ports import MemberContact
from iam_platform.core.clock import FixedClock
from tests.unit.tenancy.test_invite_member import NOW, _seed_inviter
from tests.unit.tenant_authz.fakes import FakeTenantUnitOfWork


async def test_a_member_manager_sees_names_for_this_tenant() -> None:
    uow = FakeTenantUnitOfWork()
    tenant_id = uuid4()
    actor = _seed_inviter(uow, tenant_id, {"tenant.users.manage"})
    (membership,) = [m for m in uow.tenant_memberships.by_id.values() if m.tenant_id == tenant_id]
    uow.member_directory.by_tenant[tenant_id] = {
        membership.id: MemberContact(email="owner@acme.test", display_name="Olu Owner")
    }

    rows = await ListTenantMembers(uow, FixedClock(NOW)).execute_with_contacts(
        ListTenantMembersQuery(actor_user_id=str(actor), tenant_id=str(tenant_id))
    )

    assert [(m.id, c.email if c else None) for m, c in rows] == [(membership.id, "owner@acme.test")]
    # Asked for this tenant's contacts, and only this tenant's.
    assert uow.member_directory.asked_for == [tenant_id]


async def test_without_the_roster_permission_no_contacts_are_read() -> None:
    uow = FakeTenantUnitOfWork()
    tenant_id = uuid4()
    actor = _seed_inviter(uow, tenant_id, {"tenant.resources.read"})

    with pytest.raises(PermissionDeniedError):
        await ListTenantMembers(uow, FixedClock(NOW)).execute_with_contacts(
            ListTenantMembersQuery(actor_user_id=str(actor), tenant_id=str(tenant_id))
        )
    assert uow.member_directory.asked_for == []
