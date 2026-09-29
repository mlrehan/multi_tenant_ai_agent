"""ListMyTenantMemberships -- the self-lookup bootstrap step, called with
``tenant_id=None`` before any tenant context is established."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from iam_platform.application.tenancy.list_memberships import (
    ListMyTenantMemberships,
    ListMyTenantMembershipsQuery,
)
from iam_platform.domain.tenancy.entities import MembershipStatus, Tenant, TenantMembership
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


class _ScopedTenants:
    """Behaves like `tenants` under RLS: only the row whose id is the scope
    the unit of work was opened with is visible. A use case that read every
    name from one scope -- or from none -- gets `None` back, exactly as it
    would from Postgres."""

    def __init__(self, owner: _ScopedUow, rows: dict[object, Tenant]) -> None:
        self._owner = owner
        self._rows = rows

    async def get_by_id(self, tenant_id: object) -> Tenant | None:
        if tenant_id != self._owner.scope:
            return None
        return self._rows.get(tenant_id)


class _ScopedUow:
    def __init__(self, memberships: list[TenantMembership], rows: dict[object, Tenant]) -> None:
        self.scope: object = None
        self.scopes_opened: list[object] = []
        self.tenants = _ScopedTenants(self, rows)
        self.tenant_memberships = self
        self._memberships = memberships

    async def list_by_user(self, user_id: object) -> list[TenantMembership]:
        return [m for m in self._memberships if m.user_id == user_id]

    def __call__(self, user_id: object, tenant_id: object) -> _ScopedUow:
        self.scope = tenant_id
        self.scopes_opened.append(tenant_id)
        return self

    async def __aenter__(self) -> _ScopedUow:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


def _membership(user_id: object, status: MembershipStatus = MembershipStatus.ACTIVE) -> TenantMembership:
    return TenantMembership(
        id=uuid4(), tenant_id=uuid4(), user_id=user_id, status=status,  # type: ignore[arg-type]
        created_at=NOW, updated_at=NOW,
    )


def _tenant(tenant_id: object, name: str) -> Tenant:
    return Tenant(
        id=tenant_id, slug=name.lower().replace(" ", "-"), display_name=name,  # type: ignore[arg-type]
        owner_user_id=uuid4(), created_at=NOW, updated_at=NOW,
    )


class TestTenantNamesForTheSwitcher:
    async def test_each_name_is_read_inside_its_own_tenants_scope(self) -> None:
        user_id = uuid4()
        a, b = _membership(user_id), _membership(user_id)
        names = {a.tenant_id: _tenant(a.tenant_id, "Acme"), b.tenant_id: _tenant(b.tenant_id, "Beta Ltd")}
        uow = _ScopedUow([a, b], names)

        result = await ListMyTenantMemberships(uow).execute_with_tenant_names(  # type: ignore[arg-type]
            ListMyTenantMembershipsQuery(user_id=str(user_id))
        )

        assert {r.tenant_display_name for r in result} == {"Acme", "Beta Ltd"}
        # The listing itself is still the unscoped bootstrap read.
        assert uow.scopes_opened[0] is None
        assert set(uow.scopes_opened[1:]) == {a.tenant_id, b.tenant_id}

    async def test_a_revoked_membership_is_not_told_the_tenants_name(self) -> None:
        user_id = uuid4()
        gone = _membership(user_id, MembershipStatus.REVOKED)
        uow = _ScopedUow([gone], {gone.tenant_id: _tenant(gone.tenant_id, "Renamed Since")})

        (result,) = await ListMyTenantMemberships(uow).execute_with_tenant_names(  # type: ignore[arg-type]
            ListMyTenantMembershipsQuery(user_id=str(user_id))
        )

        assert result.tenant_display_name is None
        assert gone.tenant_id not in uow.scopes_opened
