"""List every tenant, platform-wide -- the roster behind the admin panel's
central "Tenants" screen. Gated by `platform.tenants.create`: there is no
dedicated `platform.tenants.view` permission in the catalog, and reusing the
create permission (rather than inventing a new one, which would need a
migration + seed-data change) is defensible since anyone who can create a
tenant already needs to see the existing roster to avoid slug collisions
and monitor tenant growth.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from iam_platform.application.platform_authz.effective_permissions import (
    compute_effective_platform_state,
)
from iam_platform.application.platform_authz.exceptions import TenantListDeniedError
from iam_platform.application.platform_authz.ports import PlatformUowFactory
from iam_platform.core.clock import Clock
from iam_platform.domain.tenancy.entities import Tenant

_REQUIRED_PERMISSION = "platform.tenants.create"

# Re-exported so existing importers keep working; it lives in exceptions.py so
# api/exception_handlers.py can actually map it (see that module's docstring).
__all__ = ["ListTenants", "ListTenantsQuery", "TenantListDeniedError"]


@dataclass(frozen=True, slots=True)
class ListTenantsQuery:
    actor_user_id: str


class ListTenants:
    def __init__(self, uow_factory: PlatformUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, query: ListTenantsQuery) -> list[Tenant]:
        actor_id = UUID(query.actor_user_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id) as uow:
            actor_state = await compute_effective_platform_state(uow, actor_id, now=now)
            if _REQUIRED_PERMISSION not in actor_state.permissions:
                raise TenantListDeniedError(_REQUIRED_PERMISSION)

            return await uow.tenants.list_all()
