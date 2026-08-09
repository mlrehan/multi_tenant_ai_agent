"""Tenant resolution -- docs/07-tenant-isolation-and-rls.md §2.

**Phase 6 scope:** the ``X-Tenant-Id`` header path only (re-validated against
real membership every request, per the algorithm) -- verified subdomain/
custom-domain resolution needs ``tenant_domains``, which is deferred (see
CLAUDE.md's Phase 6 scope note). A header value is always a *candidate*,
never trusted on its own; the membership lookup below is what actually
authorizes it.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status

from iam_platform.api.deps.authn import get_container, get_current_claims
from iam_platform.api.deps.container import AppContainer
from iam_platform.application.identity.ports import AccessTokenClaims

_TENANT_NOT_FOUND = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="tenant not found")


@dataclass(frozen=True, slots=True)
class TenantContext:
    tenant_id: UUID
    membership_id: UUID


async def get_tenant_context(
    request: Request,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> TenantContext:
    candidate = request.headers.get("x-tenant-id")
    if not candidate:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="X-Tenant-Id header is required"
        )
    try:
        tenant_id = UUID(candidate)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="X-Tenant-Id must be a UUID"
        ) from exc

    async with container.tenant_uow_factory(claims.user_id, tenant_id) as uow:
        membership = await uow.tenant_memberships.get_by_tenant_and_user(tenant_id, claims.user_id)

    # Generic 404 whether the tenant doesn't exist, the user isn't a member,
    # or the membership is suspended/revoked -- never distinguish these, per
    # docs/03-threat-model.md scenario 1/2 (existence-inference prevention).
    if membership is None or not membership.is_active:
        raise _TENANT_NOT_FOUND

    return TenantContext(tenant_id=tenant_id, membership_id=membership.id)
