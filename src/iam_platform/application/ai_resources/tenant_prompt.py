# --------------------------------------------------------------
# src/iam_platform/application/ai_resources/tenant_prompt.py
# --------------------------------------------------------------

"""The complete system prompt one tenant's assistant receives.

**One assembly, two readers.** The answer path sends it to the model; the
platform administrator's preview shows it. They share this module so the
preview cannot describe a prompt the model never sees -- a preview built from
a second copy of the rules would be exactly as trustworthy as that copy's last
edit.

The prompt is the platform policy for the tenant's assistant profile
(`platform_policy`), then the tenant's own layers beneath it
(`prompt_layers`).
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from iam_platform.application.ai_resources.platform_policy import platform_policy
from iam_platform.application.ai_resources.prompt_layers import (
    PromptLayers,
    build_system_prompt,
)
from iam_platform.domain.ai_resources.assistant_profiles import AssistantProfile
from iam_platform.domain.ai_resources.chatbot import TenantChatbotSettings


@dataclass(frozen=True, slots=True)
class TenantPromptContext:
    """Everything the tenant half of the prompt is built from."""

    profile: AssistantProfile
    settings: TenantChatbotSettings | None
    display_name: str
    #: An active team with at least one active member exists.
    has_staffed_team: bool

    @property
    def handoff_available(self) -> bool:
        """The tenant allows handoff *and* someone can take it -- the same
        rule the widget uses to offer a transfer. No settings row means the
        default, which allows it."""
        allows = self.settings is None or self.settings.allow_human_handoff
        return bool(allows and self.has_staffed_team)


async def read_tenant_prompt_context(uow: object, tenant_id: UUID) -> TenantPromptContext:
    """Reads the context under the caller's tenant-scoped unit of work."""
    settings = await uow.chatbot_settings.get_for_tenant(tenant_id)  # type: ignore[attr-defined]
    profile = (
        settings.assistant_profile
        if settings is not None
        else await uow.chatbot_settings.assistant_profile(tenant_id)  # type: ignore[attr-defined]
    )
    display_name = await uow.chatbot_settings.tenant_display_name(tenant_id)  # type: ignore[attr-defined]
    active = await uow.teams.list_for_tenant(tenant_id, active_only=True)  # type: ignore[attr-defined]
    staffed = await uow.teams.staffed_team_ids(tenant_id=tenant_id)  # type: ignore[attr-defined]
    return TenantPromptContext(
        profile=profile,
        settings=settings,
        display_name=display_name or "",
        has_staffed_team=any(team.id in staffed for team in active),
    )


def compose_tenant_prompt(context: TenantPromptContext, base: str | None = None) -> str:
    """The profile's platform policy, then the tenant's layers beneath it.

    `base` is what the caller would otherwise have sent: the nursery policy,
    optionally followed by a legacy assistant's own guidance. Its leading
    nursery policy is replaced by the tenant's profile policy and anything
    after it is kept, so a legacy assistant prompt survives a profile change.
    A `base` that does not start with the nursery policy is kept whole -- it
    was built by a caller this module does not know about, and replacing text
    it cannot recognise would be a guess.
    """
    nursery = platform_policy(AssistantProfile.NURSERY)
    policy = platform_policy(context.profile)
    if base is None:
        base = policy
    elif base.startswith(nursery):
        base = policy + base[len(nursery) :]

    layers = PromptLayers.from_settings(
        context.settings,
        tenant_display_name=context.display_name,
        teams_configured=context.has_staffed_team,
        profile=context.profile,
    )
    return build_system_prompt(base, layers)
