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
from iam_platform.application.ai_resources.exceptions import (
    ChatWidgetNotFoundError,
    KnowledgeBaseNotFoundError,
    PermissionDeniedError,
    WidgetOriginNotAllowedError,
)
from iam_platform.application.ai_resources.ports import AiResourceUowFactory
from iam_platform.application.ai_resources.requester import build_requester_context
from iam_platform.core.clock import Clock
from iam_platform.domain.ai_resources.entities import ChatWidget

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

        origins = [o.strip().rstrip("/") for o in command.allowed_origins if o.strip()]
        if not origins:
            # Refused rather than stored empty. An empty allowlist yields a
            # widget that can never mint a session -- silently useless, and the
            # tenant would have no indication why.
            raise WidgetOriginNotAllowedError(
                "a chat widget needs at least one allowed origin, e.g. https://example.com"
            )

        async with self._uow_factory(actor_id, tenant_id) as uow:
            if MANAGE_WIDGET_PERMISSION not in command.permissions:
                raise PermissionDeniedError(MANAGE_WIDGET_PERMISSION)

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
