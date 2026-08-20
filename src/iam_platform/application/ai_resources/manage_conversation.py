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
from iam_platform.domain.ai_resources.entities import (
    AssistantStatus,
    Conversation,
    ConversationMessage,
)
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
    #: Turns in the whole thread, not in the page just returned. What lets a
    #: client know whether scrolling up will find anything, without fetching a
    #: page to discover it is empty.
    #:
    #: Defaulted so `GetConversation`, which does not read messages at all, is
    #: unaffected.
    total_messages: int = 0


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


#: Reading the *turns*, not just the metadata row. The same permission the
#: existing metadata read uses, deliberately: docs/16 already decided that
#: seeing someone else's conversation is one authority, and splitting it would
#: create a state where an admin can see a thread exists but never why it
#: mattered.
VIEW_ANY_CONVERSATION_PERMISSION = "tenant.conversations.view"

#: A page of turns. Bounded because a long thread is read by a person, not by
#: the retrieval path -- and an unbounded read is how one request materialises
#: a year of chat.
MAX_MESSAGE_PAGE = 100


@dataclass(frozen=True, slots=True)
class ConversationMessagesQuery:
    actor_user_id: str
    tenant_id: str
    conversation_id: str
    permissions: frozenset[str]
    limit: int = 50
    offset: int = 0
    #: Cursor for paging *backwards*: return turns before this `seq`. `None`
    #: means the newest page. A cursor rather than `offset` because the thread
    #: is live -- rows arrive between requests, and an offset counts from a
    #: position that has already moved.
    before_seq: int | None = None


class GetConversationMessages:
    """The turns of one conversation, for reopening or for oversight.

    **Content is owner-or-permission, never owner-only.** A tenant admin
    holding `tenant.conversations.view` sees the turns, because an oversight
    surface showing only "a conversation happened at 14:03" cannot answer the
    question oversight exists to answer. That is a deliberate widening of the
    Phase 7 metadata-only read, it is audited, and it stops at the tenant
    boundary like everything else.
    """

    def __init__(self, uow_factory: AiResourceUowFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(
        self, query: ConversationMessagesQuery
    ) -> tuple[ConversationView, list[ConversationMessage]]:
        actor_id = UUID(query.actor_user_id)
        tenant_id = UUID(query.tenant_id)
        conversation_id = UUID(query.conversation_id)

        async with self._uow_factory(actor_id, tenant_id) as uow:
            requester = await build_requester_context(
                uow, tenant_id=tenant_id, user_id=actor_id, permissions=query.permissions
            )
            if requester is None:
                raise ConversationNotFoundError(query.conversation_id)

            conversation = await uow.conversations.get_by_id(conversation_id)
            if conversation is None or not can_read_conversation(
                conversation_membership_id=conversation.membership_id, requester=requester
            ):
                raise ConversationNotFoundError(query.conversation_id)

            owner = is_conversation_owner(
                conversation_membership_id=conversation.membership_id, requester=requester
            )
            if not owner:
                # Reading someone else's conversation is exactly the action an
                # audit trail exists for. Recorded before the content is
                # returned, so a read that fails afterwards is still on record.
                await uow.audit.record(
                    actor_user_id=actor_id,
                    effective_user_id=actor_id,
                    tenant_id=tenant_id,
                    action="ai_resources.conversation_content_viewed",
                    resource_type="conversation",
                    resource_id=conversation_id,
                    result="success",
                    metadata={"owner_membership_id": str(conversation.membership_id)},
                )
            # The **most recent** turns, not the first ones. Reading from the
            # start froze any thread that outgrew one page: every new message
            # landed past the window, so an agent watched a visitor they could
            # no longer hear. `before_seq` pages backwards from there.
            messages = await uow.conversation_messages.list_tail(
                conversation_id=conversation_id,
                limit=min(query.limit, MAX_MESSAGE_PAGE),
                before_seq=query.before_seq,
            )
            total = await uow.conversation_messages.count_for_conversation(
                conversation_id
            )
            return (
                ConversationView(
                    conversation=conversation, is_owner=owner, total_messages=total
                ),
                messages,
            )


@dataclass(frozen=True, slots=True)
class ConversationActionCommand:
    actor_user_id: str
    tenant_id: str
    conversation_id: str
    permissions: frozenset[str]
    title: str = ""


class RenameConversation:
    """Owner-only, unlike reading.

    Oversight is a reason to *see* a conversation and not a reason to edit one:
    a renamed thread is a changed record, and an admin quietly retitling
    someone else's history is the kind of edit an audit log exists to prevent
    rather than to document.
    """

    def __init__(self, uow_factory: AiResourceUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: ConversationActionCommand) -> None:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)

        async with self._uow_factory(actor_id, tenant_id) as uow:
            requester = await build_requester_context(
                uow, tenant_id=tenant_id, user_id=actor_id, permissions=command.permissions
            )
            if requester is None:
                raise ConversationNotFoundError(command.conversation_id)
            conversation = await uow.conversations.get_by_id(UUID(command.conversation_id))
            if conversation is None or not is_conversation_owner(
                conversation_membership_id=conversation.membership_id, requester=requester
            ):
                raise ConversationNotFoundError(command.conversation_id)
            conversation.rename(command.title, now=self._clock.now())
            await uow.conversations.save(conversation)


class DeleteConversation:
    """Owner-only, and a real delete.

    The messages go with it through `ON DELETE CASCADE`. A soft delete would
    leave the turns readable to anyone holding the oversight permission, which
    is not what a person means when they delete a conversation.
    """

    def __init__(self, uow_factory: AiResourceUowFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, command: ConversationActionCommand) -> None:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)

        async with self._uow_factory(actor_id, tenant_id) as uow:
            requester = await build_requester_context(
                uow, tenant_id=tenant_id, user_id=actor_id, permissions=command.permissions
            )
            if requester is None:
                raise ConversationNotFoundError(command.conversation_id)
            conversation = await uow.conversations.get_by_id(UUID(command.conversation_id))
            if conversation is None or not is_conversation_owner(
                conversation_membership_id=conversation.membership_id, requester=requester
            ):
                raise ConversationNotFoundError(command.conversation_id)
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=actor_id,
                tenant_id=tenant_id,
                action="ai_resources.conversation_deleted",
                resource_type="conversation",
                resource_id=conversation.id,
                result="success",
                metadata=None,
            )
            await uow.conversations.delete(conversation.id)


@dataclass(frozen=True, slots=True)
class SearchConversationsQuery:
    actor_user_id: str
    tenant_id: str
    permissions: frozenset[str]
    text: str
    #: Tenant-wide rather than own-only. Honoured only if the caller actually
    #: holds the oversight permission -- the flag asks, the check decides.
    all_members: bool = False
    limit: int = 25


class SearchConversations:
    """Full-text search over the caller's own threads, or the tenant's.

    **Scope is decided from permissions, never from the request.** `all_members`
    is downgraded to own-only when the caller lacks the oversight permission,
    rather than refused: a search box that errors when you tick a box you
    cannot use is worse than one that quietly searches what you can see, and
    the result set is identical either way.
    """

    def __init__(self, uow_factory: AiResourceUowFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, query: SearchConversationsQuery) -> list[Conversation]:
        actor_id = UUID(query.actor_user_id)
        tenant_id = UUID(query.tenant_id)
        needle = query.text.strip()
        if not needle:
            return []

        async with self._uow_factory(actor_id, tenant_id) as uow:
            requester = await build_requester_context(
                uow, tenant_id=tenant_id, user_id=actor_id, permissions=query.permissions
            )
            if requester is None:
                return []
            tenant_wide = (
                query.all_members and VIEW_ANY_CONVERSATION_PERMISSION in query.permissions
            )
            ids = await uow.conversation_messages.search(
                tenant_id=tenant_id,
                membership_id=None if tenant_wide else requester.membership_id,
                text=needle,
                limit=min(query.limit, MAX_MESSAGE_PAGE),
            )
            return await uow.conversations.list_by_ids(ids)


@dataclass(frozen=True, slots=True)
class ListTenantConversationsQuery:
    actor_user_id: str
    tenant_id: str
    permissions: frozenset[str]


class ListTenantConversations:
    """Every conversation in the tenant -- the admin roster.

    Metadata only; the turns need the per-conversation read, which audits.
    """

    def __init__(self, uow_factory: AiResourceUowFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, query: ListTenantConversationsQuery) -> list[Conversation]:
        actor_id = UUID(query.actor_user_id)
        tenant_id = UUID(query.tenant_id)
        if VIEW_ANY_CONVERSATION_PERMISSION not in query.permissions:
            raise PermissionDeniedError(VIEW_ANY_CONVERSATION_PERMISSION)

        async with self._uow_factory(actor_id, tenant_id) as uow:
            requester = await build_requester_context(
                uow, tenant_id=tenant_id, user_id=actor_id, permissions=query.permissions
            )
            if requester is None:
                return []
            return await uow.conversations.list_by_tenant(tenant_id)
