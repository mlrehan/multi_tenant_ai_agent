# --------------------------------------------------------------
# src/iam_platform/application/ai_resources/manage_assistant_profiles.py
# --------------------------------------------------------------

"""The platform choosing each tenant's assistant profile, and seeing its prompt.

A profile decides which sector's rules the tenant's assistant answers under
(`domain.ai_resources.assistant_profiles`). It is the platform's decision, not
the tenant's: the sector half of the policy carries safeguarding and
professional-boundary rules, and a tenant able to switch its own nursery off
the nursery policy could remove the child-protection rules its visitors rely
on. So the column lives on `tenants`, which `app_tenant` cannot write, and
these use cases run on the platform unit of work.

**Gated like entitlements** (`platform.model_configurations.manage`): deciding
how a tenant's assistant behaves is the same authority as deciding what it
may spend, and a permission per screen produces a catalogue nobody can
reason about.

**The preview is read through the tenant's own RLS scope**, by the same code
the answer path uses (`tenant_prompt`), so what the administrator reads is what
the model is sent -- and it is audited, since it shows tenant-authored
configuration to someone outside the tenant.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from iam_platform.application.ai_resources.exceptions import (
    ChatbotSettingsInvalidError,
    ModelConfigurationManagementDeniedError,
)
from iam_platform.application.ai_resources.manage_entitlements import (
    MANAGE_ENTITLEMENTS_PERMISSION,
)
from iam_platform.application.ai_resources.ports import AiResourceUowFactory
from iam_platform.application.ai_resources.tenant_prompt import (
    compose_tenant_prompt,
    read_tenant_prompt_context,
)
from iam_platform.application.platform_authz.effective_permissions import (
    compute_effective_platform_state,
)
from iam_platform.application.platform_authz.exceptions import TenantNotFoundError
from iam_platform.application.platform_authz.ports import PlatformUowFactory
from iam_platform.core.clock import Clock
from iam_platform.domain.ai_resources.assistant_profiles import (
    AssistantProfile,
    coerce_assistant_profile,
)

MANAGE_ASSISTANT_PROFILES_PERMISSION = MANAGE_ENTITLEMENTS_PERMISSION


async def _require_permission(uow: object, actor_id: UUID, clock: Clock) -> None:
    state = await compute_effective_platform_state(uow, actor_id, now=clock.now())  # type: ignore[arg-type]
    if MANAGE_ASSISTANT_PROFILES_PERMISSION not in state.permissions:
        raise ModelConfigurationManagementDeniedError(MANAGE_ASSISTANT_PROFILES_PERMISSION)


@dataclass(frozen=True, slots=True)
class TenantAssistantProfileEntry:
    tenant_id: UUID
    display_name: str
    slug: str
    status: str
    profile: AssistantProfile


@dataclass(frozen=True, slots=True)
class ListTenantAssistantProfilesQuery:
    actor_user_id: str


class ListTenantAssistantProfiles:
    def __init__(self, uow_factory: PlatformUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(
        self, query: ListTenantAssistantProfilesQuery
    ) -> list[TenantAssistantProfileEntry]:
        actor_id = UUID(query.actor_user_id)
        async with self._uow_factory(actor_id) as uow:
            await _require_permission(uow, actor_id, self._clock)
            tenants = await uow.tenants.list_all()
        return [
            TenantAssistantProfileEntry(
                tenant_id=t.id,
                display_name=t.display_name,
                slug=t.slug,
                status=t.status.value,
                profile=coerce_assistant_profile(t.assistant_profile),
            )
            for t in tenants
            if t.deleted_at is None
        ]


@dataclass(frozen=True, slots=True)
class SetTenantAssistantProfileCommand:
    actor_user_id: str
    tenant_id: str
    profile: str


class SetTenantAssistantProfile:
    """Moves a tenant to another profile. Takes effect on the next answer.

    **An unknown value is refused, never coerced.** The read path degrades an
    unreadable stored value to the nursery default so a chatbot keeps
    working; the write path must not, or a typo would silently put a tenant on
    a profile nobody chose.
    """

    def __init__(self, uow_factory: PlatformUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: SetTenantAssistantProfileCommand) -> AssistantProfile:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        try:
            profile = AssistantProfile(command.profile)
        except ValueError:
            known = ", ".join(p.value for p in AssistantProfile)
            raise ChatbotSettingsInvalidError(
                f"{command.profile!r} is not an assistant profile; choose one of: {known}"
            ) from None

        async with self._uow_factory(actor_id) as uow:
            await _require_permission(uow, actor_id, self._clock)
            tenant = await uow.tenants.get_by_id(tenant_id)
            if tenant is None or tenant.deleted_at is not None:
                raise TenantNotFoundError(str(tenant_id))

            previous = coerce_assistant_profile(tenant.assistant_profile)
            if previous is profile:
                return profile
            tenant.set_assistant_profile(profile.value, now=self._clock.now())
            await uow.tenants.save(tenant)
            # Audited: this changes the rules a tenant's assistant follows with
            # the public, including its safeguarding rules. "Who took this
            # nursery off the nursery policy?" must have an answer.
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=actor_id,
                tenant_id=tenant_id,
                action="platform.tenant_assistant_profile.updated",
                resource_type="tenant",
                resource_id=tenant_id,
                result="success",
                metadata={"from": previous.value, "to": profile.value},
            )
        return profile


@dataclass(frozen=True, slots=True)
class TenantPromptPreview:
    tenant_id: UUID
    profile: AssistantProfile
    prompt: str
    #: Whether the tenant has saved chatbot settings, or is on every default.
    has_saved_settings: bool
    #: Whether the prompt tells the model a transfer to a person exists.
    handoff_available: bool


@dataclass(frozen=True, slots=True)
class PreviewTenantPromptQuery:
    actor_user_id: str
    tenant_id: str


class PreviewTenantPrompt:
    """The system prompt this tenant's assistant is sent, exactly.

    Everything above the retrieved sources and the visitor's question: those
    two change with every message, and the question's sources are already
    reviewable from the tenant's knowledge base.
    """

    def __init__(
        self,
        platform_uow_factory: PlatformUowFactory,
        tenant_uow_factory: AiResourceUowFactory,
        clock: Clock,
    ) -> None:
        self._platform_uow_factory = platform_uow_factory
        self._tenant_uow_factory = tenant_uow_factory
        self._clock = clock

    async def execute(self, query: PreviewTenantPromptQuery) -> TenantPromptPreview:
        actor_id = UUID(query.actor_user_id)
        tenant_id = UUID(query.tenant_id)

        async with self._platform_uow_factory(actor_id) as uow:
            await _require_permission(uow, actor_id, self._clock)
            tenant = await uow.tenants.get_by_id(tenant_id)
            if tenant is None or tenant.deleted_at is not None:
                raise TenantNotFoundError(str(tenant_id))

        # The tenant's own RLS scope, exactly as the answer path reads it.
        async with self._tenant_uow_factory(actor_id, tenant_id) as tenant_uow:
            context = await read_tenant_prompt_context(tenant_uow, tenant_id)

        preview = TenantPromptPreview(
            tenant_id=tenant_id,
            profile=context.profile,
            prompt=compose_tenant_prompt(context),
            has_saved_settings=context.settings is not None,
            handoff_available=context.handoff_available,
        )

        # Recorded before returning, like every platform read of tenant content.
        async with self._platform_uow_factory(actor_id) as uow:
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=actor_id,
                tenant_id=tenant_id,
                action="platform.tenant_assistant_prompt.viewed",
                resource_type="tenant",
                resource_id=tenant_id,
                result="success",
                metadata={"profile": context.profile.value},
            )
        return preview
