"""Platform role/permission catalog listings -- non-sensitive reference data
needed by the platform role-grant picker UI. Any authenticated platform
user may enumerate the catalog; only *granting* a role is gated (by the
self-escalation guard in `grant_platform_role.py`).
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from iam_platform.application.platform_authz.ports import PlatformUowFactory
from iam_platform.domain.platform_authz.entities import PlatformPermission, PlatformRole


@dataclass(frozen=True, slots=True)
class PlatformCatalogQuery:
    actor_user_id: str


class ListPlatformRoles:
    def __init__(self, uow_factory: PlatformUowFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, query: PlatformCatalogQuery) -> list[PlatformRole]:
        async with self._uow_factory(UUID(query.actor_user_id)) as uow:
            return await uow.platform_roles.list_all()


class ListPlatformPermissions:
    def __init__(self, uow_factory: PlatformUowFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, query: PlatformCatalogQuery) -> list[PlatformPermission]:
        async with self._uow_factory(UUID(query.actor_user_id)) as uow:
            return await uow.platform_permissions.list_all()


@dataclass(frozen=True, slots=True)
class RolePermissionMap:
    """Which permission codes each role grants, keyed by role code."""

    by_role_code: dict[str, list[str]]


class ListPlatformRolePermissions:
    """The role -> permission-code mapping for the whole platform catalog.

    Resolved in one pass rather than per-role: a Role Management screen shows
    every role at once, and `get_role_permission_codes` already takes a set of
    role ids, so N+1 round trips would be a choice rather than a constraint.
    """

    def __init__(self, uow_factory: PlatformUowFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, query: PlatformCatalogQuery) -> RolePermissionMap:
        async with self._uow_factory(UUID(query.actor_user_id)) as uow:
            roles = await uow.platform_roles.list_all()
            codes_by_id = await uow.platform_permissions.get_role_permission_codes(
                {r.id for r in roles}
            )
            return RolePermissionMap(
                by_role_code={
                    role.code: sorted(codes_by_id.get(role.id, set())) for role in roles
                }
            )
