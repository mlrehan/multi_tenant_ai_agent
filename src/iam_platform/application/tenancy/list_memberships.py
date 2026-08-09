"""'Which tenants am I in?' -- the tenant-resolution bootstrap step
(docs/07-tenant-isolation-and-rls.md §2): runs before any tenant_id is
known, relying on the RLS self-lookup exception on ``tenant_memberships``
rather than a platform bypass.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from iam_platform.application.tenant_authz.ports import TenantUowFactory
from iam_platform.domain.tenancy.entities import TenantMembership


@dataclass(frozen=True, slots=True)
class ListMyTenantMembershipsQuery:
    user_id: str


class ListMyTenantMemberships:
    def __init__(self, uow_factory: TenantUowFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, query: ListMyTenantMembershipsQuery) -> list[TenantMembership]:
        user_id = UUID(query.user_id)
        async with self._uow_factory(user_id, None) as uow:
            return await uow.tenant_memberships.list_by_user(user_id)
