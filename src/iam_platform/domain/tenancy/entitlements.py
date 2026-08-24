"""What a tenant is allowed to have, and how much of it they may spend.

**This is the platform's lever over a tenant, so every field here is
platform-owned and none of it is tenant-editable.** The one exception is
deliberate and constrained: a tenant admin may lower their *own* daily message
allowance below the platform's ceiling (`TenantChatbotSettings.daily_message_limit`
in the ai_resources module), which can only ever reduce spending.

Two rules shape the whole design and are worth stating before the fields:

**A limit governs creation, never existence.** Lowering `max_knowledge_bases`
for a tenant that already has three must not break those three -- they were
created under an entitlement that was valid at the time, and a platform that
silently disables working resources when an operator edits a number is a
platform nobody can safely administer. `may_create_*` is therefore asked at
the point of creation and nowhere else.

**`None` means unbounded, and it is not the same as `0`.** Zero is a real,
enforceable limit meaning "none at all"; `None` says the platform has chosen
not to cap this tenant. Collapsing the two would make "unlimited" unexpressible
and would turn an unset field into a total lockout.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from iam_platform.domain.shared.entity import Entity

#: What a tenant gets when the platform has not said otherwise. These are the
#: values a new tenant is provisioned with, and they are deliberately
#: restrictive: a tenant that has not been explicitly granted the ability to
#: spend the platform's money should not be able to.
DEFAULT_MAX_KNOWLEDGE_BASES = 1
DEFAULT_MAX_CHAT_WIDGETS = 1
DEFAULT_MAX_MESSAGES_PER_DAY = 1000
DEFAULT_MAX_TOKENS_PER_MONTH = 100_000


@dataclass(kw_only=True)
class TenantEntitlements(Entity):
    """One row per tenant. Absent means "never configured", not "unlimited".

    The application resolves a missing row to `defaults_for()` rather than to
    an empty object, so a tenant created before this table existed is governed
    by the documented defaults instead of accidentally escaping every limit.
    """

    tenant_id: UUID

    #: Resource ceilings. `None` => uncapped (see the module docstring).
    max_knowledge_bases: int | None = DEFAULT_MAX_KNOWLEDGE_BASES
    max_chat_widgets: int | None = DEFAULT_MAX_CHAT_WIDGETS
    max_messages_per_day: int | None = DEFAULT_MAX_MESSAGES_PER_DAY
    max_tokens_per_month: int | None = DEFAULT_MAX_TOKENS_PER_MONTH

    #: Capability flags. Default **false**: a capability the platform has not
    #: granted is one the tenant does not have. Defaulting these to true would
    #: mean every tenant created before an operator visits this screen can do
    #: everything, which is the opposite of what an entitlement is for.
    # `allow_own_provider_credentials` and `allow_create_assistant` were
    # dropped (migration f1c94a70b2d8): bring-your-own-key and assistant
    # management are no longer tenant capabilities, so a flag governing them
    # governed nothing. A toggle an operator can set that changes no behaviour
    # is worse than an absent one.
    allow_invite_members: bool = False
    allow_create_roles: bool = False

    updated_by_user_id: UUID | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def defaults_for(
        cls, tenant_id: UUID, *, now: datetime, entitlement_id: UUID
    ) -> TenantEntitlements:
        """The governing entitlements for a tenant with no stored row.

        Returned rather than written, so merely *reading* a tenant's limits
        never creates a row as a side effect. The row appears when a platform
        admin first sets something, which is also when it starts to mean
        anything.
        """
        return cls(
            id=entitlement_id,
            tenant_id=tenant_id,
            created_at=now,
            updated_at=now,
        )

    # -- creation checks -----------------------------------------------------
    #
    # Each takes the tenant's *current* count and answers whether one more is
    # permitted. Passing the count in (rather than holding it) keeps this
    # entity free of repository access and makes the check trivially testable.

    def may_create_knowledge_base(self, *, current_count: int) -> bool:
        return _within(current_count, self.max_knowledge_bases)

    def may_create_chat_widget(self, *, current_count: int) -> bool:
        return _within(current_count, self.max_chat_widgets)

    def effective_daily_message_limit(self, tenant_preference: int | None) -> int | None:
        """The daily cap actually enforced, given the tenant's own preference.

        **The tenant's number can only ever lower the platform's**, never raise
        it. A tenant admin setting 5,000 against a platform ceiling of 1,000 is
        not an error worth refusing at the API -- but it must not take effect,
        so the ceiling wins here regardless of what is stored. The write path
        refuses it too; this is the guarantee that holds even if a row was
        written before that check existed.
        """
        if tenant_preference is None:
            return self.max_messages_per_day
        if self.max_messages_per_day is None:
            return tenant_preference
        return min(tenant_preference, self.max_messages_per_day)


def _within(current: int, limit: int | None) -> bool:
    """`None` => uncapped. Otherwise room for one more."""
    return True if limit is None else current < limit
