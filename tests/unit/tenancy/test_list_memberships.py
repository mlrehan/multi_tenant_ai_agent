"""ListMyTenantMemberships -- the self-lookup bootstrap step, called with
``tenant_id=None`` before any tenant context is established."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from iam_platform.application.tenancy.list_memberships import (
    ListMyTenantMemberships,
    ListMyTenantMembershipsQuery,
)
from iam_platform.domain.tenancy.entities import MembershipStatus, TenantMembership
from tests.unit.tenant_authz.fakes import FakeTenantUnitOfWork

NOW = datetime(2026, 1, 1, tzinfo=UTC)


class TestListMyTenantMemberships:
    async def test_returns_only_the_callers_own_memberships(self) -> None:
        uow = FakeTenantUnitOfWork()
        user_id, other_user_id = uuid4(), uuid4()
        mine = TenantMembership(
            id=uuid4(),
            tenant_id=uuid4(),
            user_id=user_id,
            status=MembershipStatus.ACTIVE,
            created_at=NOW,
            updated_at=NOW,
        )
        someone_elses = TenantMembership(
            id=uuid4(),
            tenant_id=uuid4(),
            user_id=other_user_id,
            status=MembershipStatus.ACTIVE,
            created_at=NOW,
            updated_at=NOW,
        )
        uow.tenant_memberships.by_id[mine.id] = mine
        uow.tenant_memberships.by_id[someone_elses.id] = someone_elses

        use_case = ListMyTenantMemberships(uow)
        result = await use_case.execute(ListMyTenantMembershipsQuery(user_id=str(user_id)))

        assert [m.id for m in result] == [mine.id]
        assert uow.last_tenant_id is None  # bootstrap call carries no tenant context
