"""Resolving and enforcing what a tenant is allowed to do.

One module, because the alternative is the same three lines copied into six
create paths -- and the sixth is the one that gets forgotten. Every guard here
is called from inside the use case's unit of work, after authorization and
before the write, so a caller cannot reach a create path that skips it.

**Reading is not creating.** Every guard asks a `may_create_*` question and
none of them is consulted anywhere else. Lowering `max_knowledge_bases` below
what a tenant already has leaves those knowledge bases working, because
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

from datetime import UTC, tzinfo
from typing import Protocol
from uuid import UUID, uuid4

from iam_platform.application.ai_resources.exceptions import (
    EntitlementExceededError,
    FeatureNotEntitledError,
)
from iam_platform.application.ai_resources.ports import (
    AiResourceUnitOfWork,
    TenantChatbotSettingsRepository,
)
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


class _HasChatbotSettings(Protocol):
    """The one attribute `resolve_quota_zone` needs.

    Typed structurally rather than against `AiResourceUnitOfWork`, because the
    *platform* unit of work must resolve the same zone for its operator
    dashboard and is a different shape. Narrowing to what is actually used lets
    both pass without either protocol importing the other -- and states plainly
    that this function reads one repository and nothing else.
    """

    chatbot_settings: TenantChatbotSettingsRepository


async def resolve_quota_zone(
    uow: _HasChatbotSettings, *, tenant_id: UUID
) -> tzinfo:
    """The timezone this tenant's daily allowance resets on.

    **One implementation, because the value is a key.** `consume_message`,
    `release_message` and `messages_used_today` all build their Redis key from
    it: if two of them resolved it differently, one number would be enforced
    and a different one displayed, and nothing would look broken until a tenant
    counted their own messages.

    Never raises -- `quota_day_zone()` degrades a malformed name to UTC. A
    mistyped timezone must not take a tenant's chatbot down; it earns them the
    same off-by-an-hour boundary they had before this existed.

    No stored settings row means UTC, which is the column's own default.
    """
    settings = await uow.chatbot_settings.get_for_tenant(tenant_id)
    return settings.quota_day_zone() if settings is not None else UTC


async def resolve_daily_message_limit(
    uow: AiResourceUnitOfWork, *, tenant_id: UUID, clock: Clock
) -> int | None:
    """The daily AI-message cap actually enforced for this tenant.

    Two numbers, not one: the platform's ceiling on the tenant, and the
    tenant's own (lower) preference.
    `TenantEntitlements.effective_daily_message_limit` owns how they combine --
    the `min()` lives there so the widget, the console and this cannot
    eventually disagree about the same cap.

    Shared rather than reimplemented per entry point. Both the public widget
    and the authenticated Ask panel spend from the same allowance, and two
    copies of the resolution is how one of them ends up enforcing a stale rule.

    `None` means uncapped, and is deliberately not `0`, which is a real limit
    meaning "none at all".
    """
    entitlements = await resolve_entitlements(uow, tenant_id=tenant_id, clock=clock)
    settings = await uow.chatbot_settings.get_for_tenant(tenant_id)
    limit: int | None = entitlements.effective_daily_message_limit(
        settings.daily_message_limit if settings else None
    )
    return limit


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
