"""The platform operator's dashboard: spend across every tenant, in one read.

Everything here is an *aggregate over other people's numbers*, which is why it
is a separate module from `manage_entitlements.py` (one tenant's own plan) and
`manage_model_configuration.py` (the catalogue). Those answer "what is this
tenant allowed"; this answers "what is the whole platform doing right now".

**Computed live on every request, deliberately, and this is a scale tradeoff
rather than an oversight.** One Redis read per tenant and one per (tenant,
model) pair means a platform with a thousand tenants issues a few thousand
round trips to render one page. That is fine at the scale this is being
deployed at and will stop being fine; the honest fix when it does is a
scheduled rollup into a table, not a cleverer query here. Recorded so the next
person meets a documented decision rather than a mystery.

**Every usage number is `int | None`, never a silent zero.** The quota store
fails closed for *enforcement* -- an unreadable budget must not become
unlimited spending -- but a dashboard is a different job: refusing to render
the whole page because one counter is unavailable protects nothing and takes
the operator's only view of spending down with Redis. So a read that fails
degrades to `None`, the console shows `?`, and the operator can tell "nothing
spent" apart from "I could not find out".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from iam_platform.application.ai_resources.exceptions import (
    ModelConfigurationManagementDeniedError,
)
from iam_platform.application.platform_authz.effective_permissions import (
    compute_effective_platform_state,
)
from iam_platform.application.platform_authz.ports import PlatformUowFactory
from iam_platform.core.clock import Clock
from iam_platform.domain.tenancy.entitlements import TenantEntitlements

logger = logging.getLogger(__name__)

MANAGE_PERMISSION = "platform.model_configurations.manage"

#: A tenant or provider with less than this fraction of its allowance left is
#: flagged for the operator. Computed server-side rather than in the console so
#: the platform screen and the tenant's own screen cannot disagree about what
#: "running low" means -- two copies of a threshold drift the moment one is
#: edited, and the drift is invisible until someone is not warned.
LOW_REMAINING_FRACTION = 0.10


def is_running_low(*, limit: int | None, used: int | None) -> bool:
    """True when less than `LOW_REMAINING_FRACTION` of the allowance is left.

    `None` on either side is **not** low: an uncapped allowance cannot run out,
    and an unreadable counter is unknown rather than alarming. Flagging on a
    failed read would cry wolf every time Redis blinked, which is how an alert
    stops being read at all.
    """
    if limit is None or limit <= 0 or used is None:
        return False
    return (limit - used) < (limit * LOW_REMAINING_FRACTION)


@dataclass(frozen=True, slots=True)
class PlatformOverviewQuery:
    actor_user_id: str


@dataclass(slots=True)
class ProviderSpend:
    """One AI provider, summed across every model configuration using it.

    `total_tokens` is the sum of the *budgets* the platform has set, so a
    configuration with no budget contributes nothing to it -- and
    `has_unbudgeted` says so, because otherwise an operator reads a tidy
    "80% used" that silently excludes the model doing most of the spending.
    """

    provider: str
    model_count: int
    total_tokens: int | None
    used_tokens: int | None
    #: True when at least one configuration for this provider has no budget,
    #: making `total_tokens` a floor rather than the whole picture.
    has_unbudgeted: bool = False

    @property
    def remaining_tokens(self) -> int | None:
        if self.total_tokens is None or self.used_tokens is None:
            return None
        return max(0, self.total_tokens - self.used_tokens)

    @property
    def running_low(self) -> bool:
        return is_running_low(limit=self.total_tokens, used=self.used_tokens)


@dataclass(slots=True)
class TenantSpend:
    """One tenant's month-to-date tokens and today's messages.

    Carries the display name as well as the id: a table of UUIDs is not a
    dashboard, and resolving them in the console would mean a second round of
    requests for data the server already holds.
    """

    tenant_id: UUID
    slug: str
    display_name: str
    max_tokens_per_month: int | None
    used_tokens: int | None
    max_messages_per_day: int | None
    used_messages_today: int | None
    #: Per-model detail behind the tenant's total, for the drill-down. Empty
    #: when the tenant has been granted nothing.
    models: list[TenantModelSpend] = field(default_factory=list)

    @property
    def remaining_tokens(self) -> int | None:
        if self.max_tokens_per_month is None or self.used_tokens is None:
            return None
        return max(0, self.max_tokens_per_month - self.used_tokens)

    @property
    def running_low(self) -> bool:
        return is_running_low(limit=self.max_tokens_per_month, used=self.used_tokens)

    @property
    def remaining_messages_today(self) -> int | None:
        if self.max_messages_per_day is None or self.used_messages_today is None:
            return None
        return max(0, self.max_messages_per_day - self.used_messages_today)


@dataclass(frozen=True, slots=True)
class TenantModelSpend:
    """One (tenant, model configuration) pair -- the modal's rows."""

    model_configuration_id: UUID
    model_name: str
    provider: str
    token_budget_per_month: int | None
    used_tokens: int | None


@dataclass(frozen=True, slots=True)
class PlatformOverview:
    providers: list[ProviderSpend]
    tenants: list[TenantSpend]

    @property
    def tenants_running_low(self) -> int:
        return sum(1 for t in self.tenants if t.running_low)


class GetPlatformOverview:
    """Spend across every tenant and provider, for the operator's dashboard.

    Gated on `platform.model_configurations.manage` -- the same permission as
    the catalogue and the entitlements screen, and for the same reason: all
    three are the platform deciding, or reviewing, what tenants may spend. A
    read-only variant would be a reasonable future addition; inventing a second
    permission now would mean one nothing else checks.
    """

    def __init__(
        self,
        uow_factory: PlatformUowFactory,
        clock: Clock,
        token_usage: Any,
        tenant_quota: Any,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._token_usage = token_usage
        self._tenant_quota = tenant_quota

    async def execute(self, query: PlatformOverviewQuery) -> PlatformOverview:
        actor_id = UUID(query.actor_user_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id) as uow:
            state = await compute_effective_platform_state(uow, actor_id, now=now)
            if MANAGE_PERMISSION not in state.permissions:
                raise ModelConfigurationManagementDeniedError(MANAGE_PERMISSION)

            # Archived configurations are excluded: they cannot be assigned to
            # anything new, and counting a retired model's budget as available
            # headroom would overstate what the platform can still spend.
            configurations = await uow.model_configurations.list_all(
                include_archived=False
            )
            grants = {
                configuration.id: await uow.tenant_model_access.list_tenant_ids_for_configuration(
                    configuration.id
                )
                for configuration in configurations
            }
            tenants = await uow.tenants.list_all()
            stored = {e.tenant_id: e for e in await uow.tenant_entitlements.list_all()}

        by_id = {c.id: c for c in configurations}

        # -- per (tenant, model) usage, read once and reused by both views ----
        #
        # Both the provider roll-up and the per-tenant table need the same
        # numbers. Reading them once and indexing is what keeps this to one
        # Redis round trip per pair rather than two.
        pair_usage: dict[tuple[UUID, UUID], int | None] = {}
        for configuration_id, tenant_ids in grants.items():
            for tenant_id in tenant_ids:
                pair_usage[(tenant_id, configuration_id)] = await self._safe_pair(
                    tenant_id=tenant_id, model_configuration_id=configuration_id
                )

        providers = self._roll_up_providers(configurations, grants, pair_usage)

        tenant_rows: list[TenantSpend] = []
        for tenant in tenants:
            entitlements = stored.get(tenant.id) or TenantEntitlements.defaults_for(
                tenant.id, now=now, entitlement_id=uuid4()
            )
            models = [
                TenantModelSpend(
                    model_configuration_id=configuration_id,
                    model_name=by_id[configuration_id].model_name,
                    provider=by_id[configuration_id].provider,
                    token_budget_per_month=by_id[configuration_id].token_budget_per_month,
                    used_tokens=pair_usage.get((tenant.id, configuration_id)),
                )
                for configuration_id, tenant_ids in grants.items()
                if tenant.id in tenant_ids
            ]
            tenant_rows.append(
                TenantSpend(
                    tenant_id=tenant.id,
                    slug=tenant.slug,
                    display_name=tenant.display_name,
                    max_tokens_per_month=entitlements.max_tokens_per_month,
                    # The tenant-level counter, not the sum of `models`. They
                    # answer different questions: this one is what the tenant's
                    # own quota enforces, and it also covers answers made
                    # against the platform default model, which resolves no
                    # configuration and therefore appears in no per-model row.
                    used_tokens=await self._safe_tenant_tokens(tenant.id),
                    max_messages_per_day=entitlements.max_messages_per_day,
                    used_messages_today=await self._safe_messages(tenant.id),
                    models=sorted(models, key=lambda m: m.model_name),
                )
            )

        return PlatformOverview(
            providers=providers,
            # Tenants running low first: the dashboard's job is to put what
            # needs attention where it is seen without scrolling.
            tenants=sorted(
                tenant_rows,
                key=lambda t: (not t.running_low, t.display_name.lower()),
            ),
        )

    @staticmethod
    def _roll_up_providers(
        configurations: list[Any],
        grants: dict[UUID, list[UUID]],
        pair_usage: dict[tuple[UUID, UUID], int | None],
    ) -> list[ProviderSpend]:
        """Sums budgets and usage per provider.

        A configuration's budget is counted **once per tenant it is granted
        to**, because that is how the budget is actually enforced -- the
        counter is keyed by (tenant, configuration), so a model with a 100k
        budget granted to three tenants really can spend 300k.
        """
        totals: dict[str, dict[str, Any]] = {}
        for configuration in configurations:
            bucket = totals.setdefault(
                configuration.provider,
                {"models": 0, "budget": 0, "used": 0, "unbudgeted": False, "readable": True},
            )
            bucket["models"] += 1
            tenant_ids = grants.get(configuration.id, [])
            if configuration.token_budget_per_month is None:
                bucket["unbudgeted"] = True
            else:
                bucket["budget"] += configuration.token_budget_per_month * len(tenant_ids)
            for tenant_id in tenant_ids:
                used = pair_usage.get((tenant_id, configuration.id))
                if used is None:
                    # One unreadable counter makes the provider's total a
                    # guess. Reporting the partial sum as fact would understate
                    # spend, which is the direction that costs money.
                    bucket["readable"] = False
                else:
                    bucket["used"] += used

        return sorted(
            (
                ProviderSpend(
                    provider=provider,
                    model_count=bucket["models"],
                    total_tokens=bucket["budget"] if not bucket["unbudgeted"] else None,
                    used_tokens=bucket["used"] if bucket["readable"] else None,
                    has_unbudgeted=bucket["unbudgeted"],
                )
                for provider, bucket in totals.items()
            ),
            key=lambda p: p.provider,
        )

    # -- reads that degrade instead of failing -------------------------------

    async def _safe_pair(
        self, *, tenant_id: UUID, model_configuration_id: UUID
    ) -> int | None:
        try:
            return int(
                await self._token_usage.read(
                    tenant_id=tenant_id, model_configuration_id=model_configuration_id
                )
            )
        except Exception:
            logger.warning(
                "token usage unavailable for tenant %s / configuration %s",
                tenant_id,
                model_configuration_id,
            )
            return None

    async def _safe_tenant_tokens(self, tenant_id: UUID) -> int | None:
        try:
            return int(await self._tenant_quota.tokens_used_this_month(tenant_id=tenant_id))
        except Exception:
            logger.warning("monthly token usage unavailable for tenant %s", tenant_id)
            return None

    async def _safe_messages(self, tenant_id: UUID) -> int | None:
        try:
            return int(await self._tenant_quota.messages_used_today(tenant_id=tenant_id))
        except Exception:
            logger.warning("daily message usage unavailable for tenant %s", tenant_id)
            return None
