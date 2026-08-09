"""Builds the ``RequesterContext`` every AI-resource visibility check needs.

Split into its own module because every use case in this package starts the
same way -- resolve the caller's membership, read their department/team off
that row, combine with their already-resolved effective permissions -- and
because it is the single place that guarantees department/team come from the
database rather than from anything the client sent.
"""

from __future__ import annotations

from uuid import UUID

from iam_platform.application.ai_resources.ports import AiResourceUnitOfWork
from iam_platform.domain.ai_resources.policies import RequesterContext


async def build_requester_context(
    uow: AiResourceUnitOfWork,
    *,
    tenant_id: UUID,
    user_id: UUID,
    permissions: frozenset[str],
) -> RequesterContext | None:
    """``None`` when the caller has no active membership in this tenant --
    callers translate that into the same generic not-found the tenant
    resolver uses, never a distinct "you're not a member" signal.
    """
    membership = await uow.tenant_memberships.get_by_tenant_and_user(tenant_id, user_id)
    if membership is None or not membership.is_active:
        return None
    return RequesterContext(
        membership_id=membership.id,
        department_id=membership.department_id,
        team_id=membership.team_id,
        permissions=permissions,
    )
