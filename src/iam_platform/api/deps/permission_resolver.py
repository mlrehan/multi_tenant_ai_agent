"""Effective-permission resolution + ``require_*_permission`` dependency
factories -- docs/06-authorization-model.md §4b (the per-request
authorization dependency chain: AuthN -> TenantResolver -> PermissionResolver
-> route handler).
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Depends, HTTPException, status

from iam_platform.api.deps.authn import get_container, get_current_claims
from iam_platform.api.deps.container import AppContainer
from iam_platform.api.deps.tenant_resolver import TenantContext, get_tenant_context
from iam_platform.application.identity.ports import AccessTokenClaims
from iam_platform.application.platform_authz.effective_permissions import (
    ResolvePlatformEffectivePermissions,
    ResolvePlatformEffectivePermissionsQuery,
)
from iam_platform.application.tenant_authz.effective_permissions import (
    ResolveTenantEffectivePermissions,
    ResolveTenantEffectivePermissionsQuery,
)
from iam_platform.domain.impersonation.policies import is_impersonated


async def get_effective_tenant_permissions(
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> frozenset[str]:
    use_case = ResolveTenantEffectivePermissions(container.tenant_uow_factory, container.clock)
    return await use_case.execute(
        ResolveTenantEffectivePermissionsQuery(
            tenant_id=str(tenant_ctx.tenant_id),
            user_id=str(claims.user_id),
            # The `act` claim is the *only* signal that this session is a
            # platform user acting as the target -- it's set at token issue
            # time and signed, so it can't be dropped by a client hoping to
            # shed the impersonation restriction (docs/03-threat-model.md
            # scenario 9).
            is_impersonated=is_impersonated(claims.actor),
        )
    )


async def get_effective_platform_permissions(
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> frozenset[str]:
    # An impersonated session carries *no* platform permissions, full stop --
    # docs/06-authorization-model.md ("authorization uses the target's real,
    # tenant-scoped permissions, never platform permissions"). Without this,
    # a platform user impersonating another platform user would resolve that
    # target's platform rights, turning support access into an escalation path.
    if is_impersonated(claims.actor):
        return frozenset()

    use_case = ResolvePlatformEffectivePermissions(container.platform_uow_factory, container.clock)
    return await use_case.execute(
        ResolvePlatformEffectivePermissionsQuery(user_id=str(claims.user_id))
    )


def require_tenant_permission(permission_code: str) -> Callable[..., Coroutine[Any, Any, None]]:
    async def dependency(
        permissions: frozenset[str] = Depends(get_effective_tenant_permissions),
    ) -> None:
        if permission_code not in permissions:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")

    return dependency


def require_platform_permission(permission_code: str) -> Callable[..., Coroutine[Any, Any, None]]:
    async def dependency(
        permissions: frozenset[str] = Depends(get_effective_platform_permissions),
    ) -> None:
        if permission_code not in permissions:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")

    return dependency
