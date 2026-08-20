"""Resolving and enforcing what a tenant is allowed to do.

One module, because the alternative is the same three lines copied into six
create paths -- and the sixth is the one that gets forgotten. Every guard here
is called from inside the use case's unit of work, after authorization and
before the write, so a caller cannot reach a create path that skips it.

**Reading is not creating.** Every guard asks a `may_create_*` question and
none of them is consulted anywhere else. Withdrawing `allow_create_assistant`
from a tenant with three assistants leaves those three working, because
nothing on the read path asks. That is a requirement, not an accident: a
platform that silently disables working resources when an operator edits a
number is one nobody can administer safely.

**A missing row means the documented defaults, never "unlimited".** A tenant
created before this table existed resolves to `TenantEntitlements.defaults_for`
-- restrictive -- rather than escaping every limit because nobody had filled a
form in yet. The migration backfills a real row for every existing tenant, so
this path is for tenants created between a deploy and an operator's first
visit; it still has to be right.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from iam_platform.application.ai_resources.exceptions import (
    EntitlementExceededError,
    FeatureNotEntitledError,
)
from iam_platform.application.ai_resources.ports import AiResourceUnitOfWork
from iam_platform.core.clock import Clock
from iam_platform.domain.tenancy.entitlements import TenantEntitlements


async def resolve_entitlements(
    uow: AiResourceUnitOfWork, *, tenant_id: UUID, clock: Clock
) -> TenantEntitlements:
    stored = await uow.entitlements.get_for_tenant(tenant_id)
    if stored is not None:
        return stored
    return TenantEntitlements.defaults_for(
        tenant_id, now=clock.now(), entitlement_id=uuid4()
    )


async def guard_knowledge_base_quota(
    uow: AiResourceUnitOfWork, *, tenant_id: UUID, clock: Clock
) -> None:
    entitlements = await resolve_entitlements(uow, tenant_id=tenant_id, clock=clock)
    current = await uow.entitlements.count_knowledge_bases(tenant_id)
    if not entitlements.may_create_knowledge_base(current_count=current):
        raise EntitlementExceededError(
            resource="knowledge bases",
            limit=entitlements.max_knowledge_bases or 0,
            current=current,
        )


async def guard_chat_widget_quota(
    uow: AiResourceUnitOfWork, *, tenant_id: UUID, clock: Clock
) -> None:
    entitlements = await resolve_entitlements(uow, tenant_id=tenant_id, clock=clock)
    current = await uow.entitlements.count_chat_widgets(tenant_id)
    if not entitlements.may_create_chat_widget(current_count=current):
        raise EntitlementExceededError(
            resource="chat widgets",
            limit=entitlements.max_chat_widgets or 0,
            current=current,
        )


async def guard_capability(
    uow: AiResourceUnitOfWork,
    *,
    tenant_id: UUID,
    clock: Clock,
    capability: str,
) -> None:
    """Refuses an action the platform has not enabled for this tenant.

    `capability` names an attribute on `TenantEntitlements` rather than being
    a free string mapped through a dict: a typo then fails loudly at the call
    site instead of silently resolving to "not permitted" -- which would look
    exactly like a correctly-refused request and would be found only by a
    tenant complaining that a feature they pay for does not work.
    """
    entitlements = await resolve_entitlements(uow, tenant_id=tenant_id, clock=clock)
    allowed = getattr(entitlements, capability)
    if not isinstance(allowed, bool):
        raise AttributeError(f"{capability} is not a capability flag")
    if not allowed:
        raise FeatureNotEntitledError(capability)
