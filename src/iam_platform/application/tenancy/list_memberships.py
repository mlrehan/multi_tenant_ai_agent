"""'Which tenants am I in?' -- the tenant-resolution bootstrap step
(docs/07-tenant-isolation-and-rls.md §2): runs before any tenant_id is
known, relying on the RLS self-lookup exception on ``tenant_memberships``
rather than a platform bypass.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from iam_platform.application.tenant_authz.ports import TenantUowFactory
from iam_platform.domain.tenancy.entities import MembershipStatus, TenantMembership


@dataclass(frozen=True, slots=True)
class ListMyTenantMembershipsQuery:
    user_id: str


@dataclass(frozen=True, slots=True)
class MyMembership:
    """A membership plus the name of the tenant it is in -- for the tenant
    switcher, which otherwise has only a UUID to show a person."""

    membership: TenantMembership
    #: None when the membership is revoked (see `execute_with_tenant_names`)
    #: or the tenant row could not be read; the console then shows the id.
    tenant_slug: str | None
    tenant_display_name: str | None


class ListMyTenantMemberships:
    def __init__(self, uow_factory: TenantUowFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, query: ListMyTenantMembershipsQuery) -> list[TenantMembership]:
        user_id = UUID(query.user_id)
        async with self._uow_factory(user_id, None) as uow:
            return await uow.tenant_memberships.list_by_user(user_id)

    async def execute_with_tenant_names(
        self, query: ListMyTenantMembershipsQuery
    ) -> list[MyMembership]:
        """The same list, with each tenant's name read **inside that tenant's
        own RLS scope**.

        The bootstrap lookup above runs with no tenant set, and `tenants` is
        readable only as "the row whose id is the current tenant" -- so there
        is no single query that could fetch every name without a platform
        bypass. One short unit of work per membership, each scoped to that
        membership's tenant, reads exactly the rows the database would allow
        that member to see and nothing else. A person belongs to a handful of
        tenants, so the cost is a handful of primary-key reads.

        **Revoked memberships get no name.** The member has no standing in
        that tenant any more, and the tenant may have been renamed since --
        its current name is not theirs to be told. The switcher shows the id
        and the status, which is all it needs to say "you were removed".
        """
        user_id = UUID(query.user_id)
        memberships = await self.execute(query)
        result: list[MyMembership] = []
        for membership in memberships:
            slug: str | None = None
            name: str | None = None
            if membership.status is not MembershipStatus.REVOKED:
                async with self._uow_factory(user_id, membership.tenant_id) as uow:
                    tenant = await uow.tenants.get_by_id(membership.tenant_id)
                if tenant is not None:
                    slug, name = tenant.slug, tenant.display_name
            result.append(
                MyMembership(membership=membership, tenant_slug=slug, tenant_display_name=name)
            )
        return result
