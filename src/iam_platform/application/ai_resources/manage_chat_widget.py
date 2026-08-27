"""Tenant-side management of public chat widgets.

Creating a widget makes a slice of a knowledge base readable **by the open
internet**, which is a bigger step than any other action on a knowledge base.
It therefore requires modify rights on that knowledge base -- the same
authority as uploading to it -- rather than a lesser read permission.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from uuid import UUID, uuid4

from iam_platform.application.ai_resources.authorize import load_visible_knowledge_base
from iam_platform.application.ai_resources.entitlements import (
    guard_chat_widget_quota,
)
from iam_platform.application.ai_resources.exceptions import (
    ChatWidgetInUseError,
    ChatWidgetInvalidError,
    ChatWidgetNotFoundError,
    KnowledgeBaseNotFoundError,
    PermissionDeniedError,
    WidgetOriginNotAllowedError,
)
from iam_platform.application.ai_resources.ports import AiResourceUowFactory
from iam_platform.application.ai_resources.requester import build_requester_context
from iam_platform.core.clock import Clock
from iam_platform.domain.ai_resources.entities import ChatWidget, normalise_origin

#: Publishing to the internet is the same authority as changing what the
#: knowledge base contains -- not a lesser one.
MANAGE_WIDGET_PERMISSION = "tenant.documents.upload"

#: Long enough that guessing is pointless, short enough to paste into a script
#: tag. Not a secret (see the entity), but a guessable key would let anyone
#: enumerate widgets, and enumeration is a real nuisance even without secrecy.
_PUBLIC_KEY_BYTES = 24


@dataclass(frozen=True, slots=True)
class CreateChatWidgetCommand:
    actor_user_id: str
    tenant_id: str
    knowledge_base_id: str
    permissions: frozenset[str]
    name: str
    allowed_origins: list[str]
    daily_question_limit: int


class CreateChatWidget:
    def __init__(self, uow_factory: AiResourceUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: CreateChatWidgetCommand) -> ChatWidget:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        knowledge_base_id = UUID(command.knowledge_base_id)
        now = self._clock.now()

        # Reduced to bare origins on the way in, using the same helper the
        # match uses. Someone asked where their widget lives pastes the page
        # URL -- and a stored `https://site.example/a/page.html` can never
        # match the `https://site.example` a browser sends, so the widget is
        # dead on arrival with no error to explain it. Normalising here means
        # the stored value is the thing that will actually be compared.
        # `dict.fromkeys` dedupes while keeping the tenant's ordering: two
        # pages on one site collapse to one origin rather than a duplicate.
        origins = list(
            dict.fromkeys(
                normalised
                for o in command.allowed_origins
                if (normalised := normalise_origin(o))
            )
        )
        if not origins:
            # Refused rather than stored empty. An empty allowlist yields a
            # widget that can never mint a session -- silently useless, and the
            # tenant would have no indication why. This also catches an entry
            # with no scheme (`site.example`), which `normalise_origin`
            # deliberately refuses to guess at.
            raise WidgetOriginNotAllowedError(
                "a chat widget needs at least one allowed website address "
                "including https://, e.g. https://example.com"
            )

        async with self._uow_factory(actor_id, tenant_id) as uow:
            if MANAGE_WIDGET_PERMISSION not in command.permissions:
                raise PermissionDeniedError(MANAGE_WIDGET_PERMISSION)

            # Permission first, then plan: a caller who lacks the permission
            # must be told that, not that they are at a limit -- the two send
            # them to different people for a fix.
            await guard_chat_widget_quota(uow, tenant_id=tenant_id, clock=self._clock)

            requester = await build_requester_context(
                uow, tenant_id=tenant_id, user_id=actor_id, permissions=command.permissions
            )
            if requester is None:
                raise KnowledgeBaseNotFoundError(command.knowledge_base_id)

            await load_visible_knowledge_base(
                uow,
                knowledge_base_id=knowledge_base_id,
                requester=requester,
                for_modification=True,
            )

            widget = ChatWidget(
                id=uuid4(),
                tenant_id=tenant_id,
                knowledge_base_id=knowledge_base_id,
                name=command.name,
                public_key=f"wk_{secrets.token_urlsafe(_PUBLIC_KEY_BYTES)}",
                allowed_origins=origins,
                daily_question_limit=command.daily_question_limit,
                created_by_membership_id=requester.membership_id,
                created_at=now,
                updated_at=now,
            )
            await uow.chat_widgets.add(widget)
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=actor_id,
                tenant_id=tenant_id,
                action="ai_resources.chat_widget_created",
                resource_type="chat_widget",
                resource_id=widget.id,
                result="success",
                # Which knowledge base was made publicly readable, and where it
                # may be embedded. Exactly what an incident review asks.
                metadata={
                    "knowledge_base_id": str(knowledge_base_id),
                    "allowed_origins": origins,
                },
            )
            return widget


@dataclass(frozen=True, slots=True)
class SetChatWidgetStatusCommand:
    actor_user_id: str
    tenant_id: str
    widget_id: str
    permissions: frozenset[str]
    enabled: bool


class SetChatWidgetStatus:
    """Turns a widget off, or back on.

    This is the control the rest of the public surface's design leans on. The
    origin allowlist is only honest against browsers and the daily cap only
    bounds spending after the fact -- when a widget is being abused, "switch it
    off" is the response, and it has to be reachable by the tenant admin at the
    moment they need it. Until this existed it required a database session,
    which is not an incident response.

    Both directions go through one use case rather than two. Enable and disable
    differ by a boolean and share every check; splitting them would duplicate
    the authorization and leave two places for it to drift.
    """

    def __init__(self, uow_factory: AiResourceUowFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, command: SetChatWidgetStatusCommand) -> ChatWidget:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        widget_id = UUID(command.widget_id)

        async with self._uow_factory(actor_id, tenant_id) as uow:
            if MANAGE_WIDGET_PERMISSION not in command.permissions:
                raise PermissionDeniedError(MANAGE_WIDGET_PERMISSION)

            widget = await uow.chat_widgets.get_for_tenant(tenant_id, widget_id)
            if widget is None:
                raise ChatWidgetNotFoundError(command.widget_id)

            if command.enabled:
                widget.enable()
            else:
                widget.disable()
            await uow.chat_widgets.update(widget)

            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=actor_id,
                tenant_id=tenant_id,
                action="ai_resources.chat_widget_status_changed",
                resource_type="chat_widget",
                resource_id=widget.id,
                result="success",
                # Turning a public endpoint on or off is exactly the kind of
                # event an incident review reconstructs a timeline from.
                metadata={"status": widget.status.value},
            )
            return widget


@dataclass(frozen=True, slots=True)
class UpdateChatWidgetCommand:
    actor_user_id: str
    tenant_id: str
    widget_id: str
    permissions: frozenset[str]
    name: str
    allowed_origins: list[str]
    daily_question_limit: int


class UpdateChatWidget:
    """Edits a widget's operational settings: name, origins, daily cap.

    **Separate from `UpdateWidgetPresentation`, and the split is meaningful.**
    That one owns how the widget *looks* to a visitor (name shown in the
    bubble, avatar, greeting). This one owns what it is *allowed to do* -- which
    sites may embed it and how much it may spend in a day. They are edited from
    different screens by people asking different questions, and the origin list
    in particular is a security control rather than a presentation choice.

    Being able to edit the origin list is not a nicety: get it wrong at
    creation and the widget answers "this chat is not enabled for this website"
    on the very page it was made for, and until this existed the only remedy
    was to create a second widget -- because deleting the first was not
    possible either.

    The public key is deliberately **not** editable. It is embedded in script
    tags on sites this console does not control, and rotating it would silently
    break every page already carrying it. A widget that needs a new key is a
    new widget.
    """

    def __init__(self, uow_factory: AiResourceUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: UpdateChatWidgetCommand) -> ChatWidget:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        widget_id = UUID(command.widget_id)

        name = command.name.strip()
        if not name:
            raise ChatWidgetInvalidError("a chat widget needs a name")

        # Normalised with the same helper the origin *match* uses, so the value
        # stored here is the value that will actually be compared. See
        # `CreateChatWidget` for why a pasted page URL would otherwise never
        # match anything a browser sends.
        origins = list(
            dict.fromkeys(
                normalised
                for o in command.allowed_origins
                if (normalised := normalise_origin(o))
            )
        )
        if not origins:
            raise ChatWidgetInvalidError(
                "a chat widget needs at least one allowed website address "
                "including https://, e.g. https://example.com"
            )

        async with self._uow_factory(actor_id, tenant_id) as uow:
            if MANAGE_WIDGET_PERMISSION not in command.permissions:
                raise PermissionDeniedError(MANAGE_WIDGET_PERMISSION)

            widget = await uow.chat_widgets.get_for_tenant(tenant_id, widget_id)
            if widget is None:
                # 404, never 403: a widget in another tenant must not be
                # provable to exist by the shape of the refusal.
                raise ChatWidgetNotFoundError(command.widget_id)

            previous_origins = list(widget.allowed_origins)
            widget.name = name
            widget.allowed_origins = origins
            widget.daily_question_limit = command.daily_question_limit
            widget.updated_at = self._clock.now()
            await uow.chat_widgets.update(widget)

            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=actor_id,
                tenant_id=tenant_id,
                action="ai_resources.chat_widget_updated",
                resource_type="chat_widget",
                resource_id=widget.id,
                result="success",
                # The origin list is recorded before and after: widening who
                # may embed a public endpoint is the change an incident review
                # would want to place on a timeline.
                metadata={
                    "allowed_origins_before": previous_origins,
                    "allowed_origins_after": origins,
                    "daily_question_limit": widget.daily_question_limit,
                },
            )
            return widget


@dataclass(frozen=True, slots=True)
class DeleteChatWidgetCommand:
    actor_user_id: str
    tenant_id: str
    widget_id: str
    permissions: frozenset[str]


class DeleteChatWidget:
    """Removes a widget that has never been used.

    **Refuses when conversations reference it, rather than deleting them or
    orphaning them.** `conversations.widget_id` is a real foreign key with no
    cascade, so Postgres would refuse this anyway -- but refusing *here*, with
    a message naming the count and pointing at the alternative, is the
    difference between a tenant understanding what happened and seeing a 500.

    The alternative is not a workaround: disabling already stops a widget
    minting sessions immediately, which is the whole of what "make it stop"
    requires. What disabling does not do is tidy the list, and that is exactly
    what this is for -- the test widgets that never took a conversation.

    Deleting a used widget would mean either destroying transcripts a tenant
    may be required to retain, or silently detaching them from the embed that
    produced them. Neither is a decision to make on someone's behalf from a
    dialog with a bin icon on it.
    """

    def __init__(self, uow_factory: AiResourceUowFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, command: DeleteChatWidgetCommand) -> None:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        widget_id = UUID(command.widget_id)

        async with self._uow_factory(actor_id, tenant_id) as uow:
            if MANAGE_WIDGET_PERMISSION not in command.permissions:
                raise PermissionDeniedError(MANAGE_WIDGET_PERMISSION)

            widget = await uow.chat_widgets.get_for_tenant(tenant_id, widget_id)
            if widget is None:
                raise ChatWidgetNotFoundError(command.widget_id)

            used = await uow.chat_widgets.count_conversations(
                tenant_id=tenant_id, widget_id=widget_id
            )
            if used:
                raise ChatWidgetInUseError(
                    f"this widget has {used} conversation(s) and cannot be "
                    f"deleted without losing them. Disable it instead -- that "
                    f"stops it answering immediately."
                )

            await uow.chat_widgets.delete(tenant_id=tenant_id, widget_id=widget_id)

            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=actor_id,
                tenant_id=tenant_id,
                action="ai_resources.chat_widget_deleted",
                resource_type="chat_widget",
                resource_id=widget_id,
                result="success",
                # The public key is recorded because it is the thing still
                # sitting in a script tag on someone's website: after this, that
                # tag stops working, and this row is what explains why.
                metadata={"public_key": widget.public_key, "name": widget.name},
            )


@dataclass(frozen=True, slots=True)
class ListChatWidgetsQuery:
    actor_user_id: str
    tenant_id: str
    permissions: frozenset[str]


class ListChatWidgets:
    def __init__(self, uow_factory: AiResourceUowFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, query: ListChatWidgetsQuery) -> list[ChatWidget]:
        actor_id = UUID(query.actor_user_id)
        tenant_id = UUID(query.tenant_id)

        async with self._uow_factory(actor_id, tenant_id) as uow:
            if MANAGE_WIDGET_PERMISSION not in query.permissions:
                raise PermissionDeniedError(MANAGE_WIDGET_PERMISSION)
            return await uow.chat_widgets.list_for_tenant(tenant_id)
