"""The chatbot's brief, and the per-widget presentation.

`manage_chatbot.py` owns the company-wide policy fields; this module owns the
two screens that sit beside them -- Behaviour/Tone (tenant-wide, one brief per
company) and Identity (per embed, because a nursery may run one widget on its
parent portal and another on its public site).

The brief used to live on `ai_assistants` and moved here when assistant
management was withdrawn from the tenant surface.

**Both are tenant-editable and therefore untrusted at prompt-assembly time.**
Nothing here is a security boundary -- the boundaries are the prompt builder's
fencing, the enum mapping that keeps personality out of the prompt as free
text, and the avatar allowlist that keeps a tenant from pointing every
visitor's browser at an arbitrary origin.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from iam_platform.application.ai_resources.exceptions import (
    ChatbotSettingsInvalidError,
    ChatWidgetNotFoundError,
    PermissionDeniedError,
)
from iam_platform.application.ai_resources.ports import AiResourceUowFactory
from iam_platform.core.clock import Clock
from iam_platform.domain.ai_resources.chatbot import (
    AVATAR_KEYS,
    Personality,
    ResponseLength,
    TenantChatbotSettings,
)
from iam_platform.domain.ai_resources.entities import ChatWidget

MANAGE_WIDGET_PERMISSION = "tenant.documents.upload"
#: Same gate as every other field on the chatbot screen. Deliberately the same
#: constant `manage_chatbot.py` uses, not a copy: two spellings of one rule is
#: how they drift.
MANAGE_CHATBOT_PERMISSION = MANAGE_WIDGET_PERMISSION


@dataclass(frozen=True, slots=True)
class UpdateChatbotBehaviourCommand:
    actor_user_id: str
    tenant_id: str
    permissions: frozenset[str]
    role_instructions: str
    avoid_instructions: str
    personality: str
    response_length: str


class UpdateChatbotBehaviour:
    """The Behaviour and Tone tabs.

    **Writes to `tenant_chatbot_settings`, not to an assistant.** These fields
    lived on `ai_assistants` until assistant management was withdrawn from
    tenants; a tenant who cannot see an assistant cannot be asked to own one in
    order to set a tone. There is one brief per company now, which is also what
    the console has always presented -- the per-assistant split was only ever
    reachable by tenants running more than one assistant.

    Authorized with `MANAGE_CHATBOT_PERMISSION`, the same gate as every other
    field on this screen, rather than the assistant visibility policy it used
    to borrow. That policy answered "may this caller edit *this resource*",
    which no longer has a resource to name.
    """

    def __init__(self, uow_factory: AiResourceUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: UpdateChatbotBehaviourCommand) -> TenantChatbotSettings:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        now = self._clock.now()

        role = command.role_instructions.strip()
        avoid = command.avoid_instructions.strip()
        # Deliberately unbounded -- see the note in domain/ai_resources/chatbot.py
        # and migration e9c47b13f0a2. The provider's context window is the real
        # limit and it moves; a number here could only ever disagree with it.
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
            if MANAGE_CHATBOT_PERMISSION not in command.permissions:
                raise PermissionDeniedError(MANAGE_CHATBOT_PERMISSION)

            settings = await uow.chatbot_settings.get_for_tenant(tenant_id)
            if settings is None:
                settings = TenantChatbotSettings(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    created_at=now,
                    updated_at=now,
                )
            settings.role_instructions = role or None
            settings.avoid_instructions = avoid or None
            settings.personality = personality
            settings.response_length = response_length
            settings.updated_at = now
            await uow.chatbot_settings.upsert(settings)
        return settings


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

            # No assistant binding any more: every widget answers with the
            # platform's default model and this tenant's own brief from
            # `tenant_chatbot_settings`. `chat_widgets.assistant_id` is left in
            # the schema holding whatever historical value it already had --
            # nothing reads it on the answer path, and clearing it would
            # destroy a record of how past conversations were answered.
            widget.chatbot_name = (command.chatbot_name or "").strip() or None
            widget.chatbot_title = (command.chatbot_title or "").strip() or None
            widget.avatar_key = command.avatar_key
            widget.greeting = (command.greeting or "").strip() or None
            widget.show_quick_reply_suggestions = command.show_quick_reply_suggestions
            widget.updated_at = now
            await uow.chat_widgets.update(widget)
        return widget
