"""The two halves of chatbot configuration that are not tenant-wide.

`manage_chatbot.py` owns the company-wide settings; these own the per-assistant
brief and the per-widget presentation. Split along the same lines as the schema,
for the same reason: an assistant's role is how it thinks and a widget's name is
how it looks on one page, and a tenant may reasonably run two of each.

**Both are tenant-editable and therefore untrusted at prompt-assembly time.**
Nothing here is a security boundary -- the boundaries are the length caps
(enforced here, in the domain, and by a CHECK), the enum mapping that keeps
personality out of the prompt as free text, and the avatar allowlist that keeps
a tenant from pointing every visitor's browser at an arbitrary origin.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from iam_platform.application.ai_resources.authorize import load_visible_assistant
from iam_platform.application.ai_resources.exceptions import (
    AssistantNotFoundError,
    ChatbotSettingsInvalidError,
    ChatWidgetNotFoundError,
    PermissionDeniedError,
)
from iam_platform.application.ai_resources.ports import AiResourceUowFactory
from iam_platform.application.ai_resources.requester import build_requester_context
from iam_platform.core.clock import Clock
from iam_platform.domain.ai_resources.chatbot import (
    AVATAR_KEYS,
    Personality,
    ResponseLength,
)
from iam_platform.domain.ai_resources.entities import AiAssistant, ChatWidget

MANAGE_WIDGET_PERMISSION = "tenant.documents.upload"


@dataclass(frozen=True, slots=True)
class UpdateAssistantBehaviourCommand:
    actor_user_id: str
    tenant_id: str
    assistant_id: str
    permissions: frozenset[str]
    role_instructions: str
    avoid_instructions: str
    personality: str
    response_length: str


class UpdateAssistantBehaviour:
    """The Behaviour and Tone tabs.

    Authorized exactly like `UpdateAssistant` -- through
    `load_visible_assistant(for_modification=True)` -- rather than with a new
    permission. Changing an assistant's brief *is* editing the assistant; a
    caller who may rename it may equally tell it what to avoid.
    """

    def __init__(self, uow_factory: AiResourceUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: UpdateAssistantBehaviourCommand) -> AiAssistant:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        now = self._clock.now()

        role = command.role_instructions.strip()
        avoid = command.avoid_instructions.strip()
        # Validated against the enum here so a bad value is refused at the API
        # with a clear message. The *prompt* builder degrades an unknown stored
        # value to the default rather than trusting it -- two different jobs:
        # this one tells the caller, that one protects the prompt.
        try:
            personality = Personality(command.personality)
            response_length = ResponseLength(command.response_length)
        except ValueError as exc:
            raise ChatbotSettingsInvalidError(str(exc)) from exc

        async with self._uow_factory(actor_id, tenant_id) as uow:
            requester = await build_requester_context(
                uow,
                tenant_id=tenant_id,
                user_id=actor_id,
                permissions=command.permissions,
            )
            if requester is None:
                raise AssistantNotFoundError(command.assistant_id)

            assistant = await load_visible_assistant(
                uow,
                assistant_id=UUID(command.assistant_id),
                requester=requester,
                for_modification=True,
            )
            assistant.role_instructions = role or None
            assistant.avoid_instructions = avoid or None
            assistant.personality = personality
            assistant.response_length = response_length
            assistant.updated_at = now
            await uow.assistants.save(assistant)
        return assistant


@dataclass(frozen=True, slots=True)
class UpdateWidgetPresentationCommand:
    actor_user_id: str
    tenant_id: str
    widget_id: str
    permissions: frozenset[str]
    chatbot_name: str | None
    chatbot_title: str | None
    avatar_key: str | None
    greeting: str | None
    show_quick_reply_suggestions: bool
    assistant_id: str | None


class UpdateWidgetPresentation:
    """The Identity and Reply Experience tabs, for one embed."""

    def __init__(self, uow_factory: AiResourceUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: UpdateWidgetPresentationCommand) -> ChatWidget:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        now = self._clock.now()

        if command.avatar_key is not None and command.avatar_key not in AVATAR_KEYS:
            # An allowlist, not a URL. Storing a URL here would let a tenant
            # point every visitor's browser at an arbitrary third-party origin
            # from their own customers' pages -- a tracking pixel the tenant's
            # customers serve on the tenant's behalf.
            raise ChatbotSettingsInvalidError(
                f"unknown avatar; choose one of: {', '.join(AVATAR_KEYS)}"
            )

        async with self._uow_factory(actor_id, tenant_id) as uow:
            if MANAGE_WIDGET_PERMISSION not in command.permissions:
                raise PermissionDeniedError(MANAGE_WIDGET_PERMISSION)

            widget = await uow.chat_widgets.get_for_tenant(
                tenant_id, UUID(command.widget_id)
            )
            if widget is None:
                raise ChatWidgetNotFoundError(command.widget_id)

            assistant_id: UUID | None = None
            if command.assistant_id:
                requester = await build_requester_context(
                    uow,
                    tenant_id=tenant_id,
                    user_id=actor_id,
                    permissions=command.permissions,
                )
                if requester is None:
                    raise AssistantNotFoundError(command.assistant_id)
                # Loaded through the visibility policy, so a widget cannot be
                # pointed at an assistant the caller cannot see. The composite
                # FK stops a *cross-tenant* id at the database; this stops a
                # same-tenant one the caller has no business using.
                assistant = await load_visible_assistant(
                    uow,
                    assistant_id=UUID(command.assistant_id),
                    requester=requester,
                    for_modification=False,
                )
                assistant_id = assistant.id

            widget.assistant_id = assistant_id
            widget.chatbot_name = (command.chatbot_name or "").strip() or None
            widget.chatbot_title = (command.chatbot_title or "").strip() or None
            widget.avatar_key = command.avatar_key
            widget.greeting = (command.greeting or "").strip() or None
            widget.show_quick_reply_suggestions = command.show_quick_reply_suggestions
            widget.updated_at = now
            await uow.chat_widgets.update(widget)
        return widget
