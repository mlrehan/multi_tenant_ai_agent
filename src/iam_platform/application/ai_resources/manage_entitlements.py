"""Platform-side governance of what each tenant may have and spend.

Runs on the **platform** unit of work (BYPASSRLS), like
`manage_model_configuration.py` and for the same reason: this is a platform
operator acting *on* a tenant, so there is no tenant RLS context to run under
and pretending otherwise would mean setting a tenant context for a caller who
is not a member of it.

The tenant's own read of these numbers is a separate, narrower path
(`GetTenantEntitlements` below), which runs under tenant RLS and returns the
same figures without any way to change them. Both exist because "show me my
plan" and "change someone's plan" are different authorities that happen to
read one table.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from iam_platform.application.ai_resources.exceptions import (
    ModelConfigurationManagementDeniedError,
)
from iam_platform.application.ai_resources.ports import AiResourceUowFactory
from iam_platform.application.platform_authz.effective_permissions import (
    compute_effective_platform_state,
)
from iam_platform.application.platform_authz.ports import PlatformUowFactory
from iam_platform.core.clock import Clock
from iam_platform.domain.tenancy.entitlements import TenantEntitlements

#: Reused rather than a new permission. Governing a tenant's plan is the same
#: authority as governing the model catalogue -- both are the platform
#: deciding what a tenant may spend the platform's money on -- and a
#: permission per screen produces a catalogue nobody can reason about.
MANAGE_ENTITLEMENTS_PERMISSION = "platform.model_configurations.manage"


@dataclass(frozen=True, slots=True)
class SetTenantEntitlementsCommand:
    actor_user_id: str
    tenant_id: str
    max_knowledge_bases: int | None
    max_chat_widgets: int | None
    max_messages_per_day: int | None
    max_tokens_per_month: int | None
    allow_invite_members: bool
    allow_create_roles: bool


class SetTenantEntitlements:
    def __init__(self, uow_factory: PlatformUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: SetTenantEntitlementsCommand) -> TenantEntitlements:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id) as uow:
            # Resolved here, not taken from the caller. A permission set passed
            # in from the route is a permission set a request could shape.
            state = await compute_effective_platform_state(uow, actor_id, now=now)
            if MANAGE_ENTITLEMENTS_PERMISSION not in state.permissions:
                raise ModelConfigurationManagementDeniedError(
                    MANAGE_ENTITLEMENTS_PERMISSION
                )

            existing = await uow.tenant_entitlements.get_for_tenant(tenant_id)
            entitlements = existing or TenantEntitlements.defaults_for(
                tenant_id, now=now, entitlement_id=uuid4()
            )
            entitlements.max_knowledge_bases = command.max_knowledge_bases
            entitlements.max_chat_widgets = command.max_chat_widgets
            entitlements.max_messages_per_day = command.max_messages_per_day
            entitlements.max_tokens_per_month = command.max_tokens_per_month
            entitlements.allow_invite_members = command.allow_invite_members
            entitlements.allow_create_roles = command.allow_create_roles
            entitlements.updated_by_user_id = actor_id
            entitlements.updated_at = now

            await uow.tenant_entitlements.upsert(entitlements)
            # Audited: an entitlement change moves what a tenant may spend, and
            # "who raised this tenant's token cap to ten million?" is a
            # question an incident review has to be able to answer.
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=actor_id,
                tenant_id=tenant_id,
                action="platform.tenant_entitlements.updated",
                resource_type="tenant_entitlements",
                resource_id=entitlements.id,
                result="success",
                metadata={
                    "max_knowledge_bases": command.max_knowledge_bases,
                    "max_chat_widgets": command.max_chat_widgets,
                    "max_messages_per_day": command.max_messages_per_day,
                    "max_tokens_per_month": command.max_tokens_per_month,
                    "allow_invite_members": command.allow_invite_members,
                    "allow_create_roles": command.allow_create_roles,
                },
            )
        return entitlements


@dataclass(frozen=True, slots=True)
class ListTenantEntitlementsQuery:
    actor_user_id: str


class ListTenantEntitlements:
    """Every tenant's governing plan -- **one entry per tenant, not per row.**

    Returning only stored rows made this screen unusable for the case that
    matters most. A tenant has no `tenant_entitlements` row until someone sets
    one (`defaults_for` is deliberately returned, not written, so reading a
    plan never creates a row). The console's only edit affordance is a button
    on a listed tenant -- so with nothing listed, a plan could never be set
    for the first time through the UI at all, and a platform-wide "No tenants
    configured" was shown while tenants plainly existed.

    **The defaults are filled in here, server-side, on purpose.** They live in
    the domain (`TenantEntitlements.defaults_for`), and a client synthesising
    its own copy would silently disagree with what the platform actually
    enforces the moment those values change. Same reason namespace parsing
    lives beside the namespace builder.

    A synthesised entry is indistinguishable from a stored one by design: it
    *is* what governs that tenant, and the write path is an upsert, so saving
    one is what brings the row into existence.
    """

    def __init__(self, uow_factory: PlatformUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(
        self, query: ListTenantEntitlementsQuery
    ) -> list[TenantEntitlements]:
        actor_id = UUID(query.actor_user_id)
        now = self._clock.now()
        async with self._uow_factory(actor_id) as uow:
            state = await compute_effective_platform_state(uow, actor_id, now=now)
            if MANAGE_ENTITLEMENTS_PERMISSION not in state.permissions:
                raise ModelConfigurationManagementDeniedError(
                    MANAGE_ENTITLEMENTS_PERMISSION
                )
            stored = {e.tenant_id: e for e in await uow.tenant_entitlements.list_all()}
            tenants = await uow.tenants.list_all()

        return [
            stored.get(t.id)
            or TenantEntitlements.defaults_for(t.id, now=now, entitlement_id=uuid4())
            for t in tenants
        ]


@dataclass(frozen=True, slots=True)
class GetTenantEntitlementsQuery:
    actor_user_id: str
    tenant_id: str


@dataclass(frozen=True, slots=True)
class TenantPlanView:
    """What a tenant sees about its own plan.

    Carries the *current usage* beside each limit, which is the half that makes
    the numbers actionable: "3 knowledge bases" tells an admin nothing, "3 of
    3" tells them why the create button is disabled.

    `messages_used_today` and `tokens_used_this_month` are `None` when the
    counter could not be read -- deliberately distinct from 0, which would
    claim nothing has been spent. The console renders `?`.
    """

    entitlements: TenantEntitlements
    knowledge_bases_used: int
    chat_widgets_used: int
    messages_used_today: int | None
    tokens_used_this_month: int | None
    effective_daily_message_limit: int | None


class GetTenantEntitlements:
    """A tenant reading its own plan.

    Deliberately requires no permission beyond membership: every member sees
    disabled buttons for capabilities the tenant lacks, and a screen that
    cannot explain why is worse than one that can. There is nothing sensitive
    here -- it is the tenant's own plan, and the write path is a different
    authority entirely (and a different table grant: `app_tenant` holds SELECT
    only).
    """

    def __init__(
        self,
        uow_factory: AiResourceUowFactory,
        clock: Clock,
        quota_store: object | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._quota_store = quota_store

    async def execute(self, query: GetTenantEntitlementsQuery) -> TenantPlanView:
        from iam_platform.application.ai_resources.entitlements import (
            resolve_entitlements,
        )

        actor_id = UUID(query.actor_user_id)
        tenant_id = UUID(query.tenant_id)

        async with self._uow_factory(actor_id, tenant_id) as uow:
            entitlements = await resolve_entitlements(
                uow, tenant_id=tenant_id, clock=self._clock
            )
            settings = await uow.chatbot_settings.get_for_tenant(tenant_id)
            # No assistant count: there is no `max_assistants` to compare it
            # against and no route for a tenant to create one, so reporting it
            # would be a number nobody can act on.
            counts = (
                await uow.entitlements.count_knowledge_bases(tenant_id),
                await uow.entitlements.count_chat_widgets(tenant_id),
            )

        # **The same zone the writes used.** This is the number the tenant
        # reads on their dashboard; resolving the day differently from the
        # answer path would show one figure while enforcing another, and
        # nothing would look broken until they counted their own messages.
        messages_used = await self._safe_messages(tenant_id, settings)
        tokens_used = await self._safe_tokens(tenant_id)
        return TenantPlanView(
            entitlements=entitlements,
            knowledge_bases_used=counts[0],
            chat_widgets_used=counts[1],
            messages_used_today=messages_used,
            tokens_used_this_month=tokens_used,
            effective_daily_message_limit=entitlements.effective_daily_message_limit(
                settings.daily_message_limit if settings else None
            ),
        )

    # Reads fail **open** here and only here: this is a display. Refusing to
    # render a plan page because Redis blinked protects nothing and takes the
    # console down with the cache. Enforcement, on the answer path, still fails
    # closed -- the asymmetry is the point.
    async def _safe_messages(
        self, tenant_id: UUID, settings: object | None
    ) -> int | None:
        if self._quota_store is None:
            return None
        # Taken from the settings row already loaded above rather than read
        # again -- one fewer round trip, and one fewer chance of resolving a
        # different zone than the enforcement path did.
        zone = settings.quota_day_zone() if settings is not None else None  # type: ignore[attr-defined]
        try:
            used: int = await self._quota_store.messages_used_today(  # type: ignore[attr-defined]
                tenant_id=tenant_id, zone=zone
            )
            return used
        except Exception:
            return None

    async def _safe_tokens(self, tenant_id: UUID) -> int | None:
        if self._quota_store is None:
            return None
        try:
            return await self._quota_store.tokens_used_this_month(tenant_id=tenant_id)  # type: ignore[attr-defined]
        except Exception:
            return None
