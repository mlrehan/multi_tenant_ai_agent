"""Conversation lifecycle -- docs/16-schema-ai-resources.md.

Conversations are the one AI resource whose access rule is *ownership*, not
visibility: a conversation belongs to the membership that started it. Holders
of ``tenant.conversations.view`` (Auditor/Administrator) can retrieve it, but
docs/16 is explicit that this is metadata-only -- so every such access is
audited, and the API schema is what withholds content.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from iam_platform.application.ai_resources.authorize import load_visible_assistant
from iam_platform.application.ai_resources.exceptions import (
    AssistantNotFoundError,
    ConversationNotFoundError,
    PermissionDeniedError,
)
from iam_platform.application.ai_resources.ports import AiResourceUowFactory
from iam_platform.application.ai_resources.requester import build_requester_context
from iam_platform.core.clock import Clock
from iam_platform.domain.ai_resources.entities import AssistantStatus, Conversation
from iam_platform.domain.ai_resources.policies import (
    can_read_conversation,
    is_conversation_owner,
)

START_CONVERSATION_PERMISSION = "tenant.conversations.create"


@dataclass(frozen=True, slots=True)
class StartConversationCommand:
    actor_user_id: str
    tenant_id: str
    assistant_id: str
    permissions: frozenset[str]
    title: str | None = None


class StartConversation:
    def __init__(self, uow_factory: AiResourceUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: StartConversationCommand) -> UUID:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id, tenant_id) as uow:
            if START_CONVERSATION_PERMISSION not in command.permissions:
                raise PermissionDeniedError(START_CONVERSATION_PERMISSION)

            requester = await build_requester_context(
                uow, tenant_id=tenant_id, user_id=actor_id, permissions=command.permissions
            )
            if requester is None:
                raise AssistantNotFoundError(command.assistant_id)

            # Read access to the assistant is the gate -- you may converse
            # with any assistant you can see, without being able to edit it.
            assistant = await load_visible_assistant(
                uow, assistant_id=UUID(command.assistant_id), requester=requester
            )
            if assistant.status != AssistantStatus.PUBLISHED:
                # A draft/archived assistant isn't conversable. Reported as
                # not-found rather than a distinct error so the draft
                # pipeline of another team isn't externally observable.
                raise AssistantNotFoundError(command.assistant_id)

            conversation = Conversation(
                id=uuid4(),
                tenant_id=tenant_id,
                assistant_id=assistant.id,
                membership_id=requester.membership_id,
                title=command.title,
                created_at=now,
                updated_at=now,
            )
            await uow.conversations.add(conversation)
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=actor_id,
                tenant_id=tenant_id,
                action="ai_resources.conversation_started",
                resource_type="conversation",
                resource_id=conversation.id,
                result="success",
                metadata={"assistant_id": str(assistant.id)},
            )
            return conversation.id


@dataclass(frozen=True, slots=True)
class ListMyConversationsQuery:
    actor_user_id: str
    tenant_id: str
    permissions: frozenset[str]


class ListMyConversations:
    def __init__(self, uow_factory: AiResourceUowFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, query: ListMyConversationsQuery) -> list[Conversation]:
        actor_id = UUID(query.actor_user_id)
        tenant_id = UUID(query.tenant_id)

        async with self._uow_factory(actor_id, tenant_id) as uow:
            requester = await build_requester_context(
                uow, tenant_id=tenant_id, user_id=actor_id, permissions=query.permissions
            )
            if requester is None:
                return []
            return await uow.conversations.list_by_membership(requester.membership_id)


@dataclass(frozen=True, slots=True)
class GetConversationQuery:
    actor_user_id: str
    tenant_id: str
    conversation_id: str
    permissions: frozenset[str]


@dataclass(frozen=True, slots=True)
class ConversationView:
    """``is_owner=False`` means the caller reached this row through
    ``tenant.conversations.view`` -- the API layer uses the flag to serve a
    metadata-only representation, per docs/16.
    """

    conversation: Conversation
    is_owner: bool


class GetConversation:
    def __init__(self, uow_factory: AiResourceUowFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, query: GetConversationQuery) -> ConversationView:
        actor_id = UUID(query.actor_user_id)
        tenant_id = UUID(query.tenant_id)

        async with self._uow_factory(actor_id, tenant_id) as uow:
            requester = await build_requester_context(
                uow, tenant_id=tenant_id, user_id=actor_id, permissions=query.permissions
            )
            if requester is None:
                raise ConversationNotFoundError(query.conversation_id)

            conversation = await uow.conversations.get_by_id(UUID(query.conversation_id))
            if conversation is None or not can_read_conversation(
                conversation_membership_id=conversation.membership_id, requester=requester
            ):
                raise ConversationNotFoundError(query.conversation_id)

            owner = is_conversation_owner(
                conversation_membership_id=conversation.membership_id, requester=requester
            )
            if not owner:
                # docs/16: "access by anyone other than the owning membership
                # is audited" -- the tenant-scope analogue of the
                # cross-tenant platform-action audit requirement.
                await uow.audit.record(
                    actor_user_id=actor_id,
                    effective_user_id=None,
                    tenant_id=tenant_id,
                    action="ai_resources.conversation_accessed_by_non_owner",
                    resource_type="conversation",
                    resource_id=conversation.id,
                    result="success",
                )
            return ConversationView(conversation=conversation, is_owner=owner)
