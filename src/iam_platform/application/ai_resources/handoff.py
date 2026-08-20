"""Human handoff: requesting it, routing it, claiming it, and coming back.

**Every transition here is a real write, never a message that says one
happened.** The AI telling a visitor "I'm transferring you to Admissions" while
`state` stays `ai_active` is the failure mode this module exists to prevent:
the visitor waits for a human who was never told, and the conversation sits in
nobody's queue. So `route_to_team` moves the row first and the reassuring
sentence is generated from the result.

**The AI stops, and only an explicit action restarts it.** `Conversation.
ai_may_reply` is true in exactly one state. Nothing about a visitor sending
another message changes that -- which is the case that would otherwise have the
AI talking over an agent mid-conversation. `ReturnConversationToAi` is the one
supported way back, and it is a deliberate action by a person, so it can be
audited as one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID, uuid4

from iam_platform.application.ai_resources.exceptions import (
    ConversationAlreadyClaimedError,
    ConversationNotFoundError,
    HandoffNotAvailableError,
    PermissionDeniedError,
    TeamNotFoundError,
)
from iam_platform.application.ai_resources.ports import (
    AiResourceUnitOfWork,
    AiResourceUowFactory,
    ConversationEventPublisher,
    TypingIndicatorStore,
)
from iam_platform.core.clock import Clock
from iam_platform.domain.ai_resources.entities import (
    ConversationMessage,
    ConversationState,
    HandoffInitiator,
    MessageRole,
)
from iam_platform.domain.tenancy.teams import TenantTeam

logger = logging.getLogger("iam_platform.application.ai_resources.handoff")

#: Reading and answering conversations that are not yours is the oversight
#: authority the console already gates the conversation roster on.
AGENT_PERMISSION = "tenant.conversations.view"

#: Sees *every* team's queue rather than only their own teams'.
#:
#: A second permission rather than a flag on the first, because "may work the
#: inbox" and "may see the whole tenant's inbox" are different authorities: an
#: agent staffing Admissions has no business reading a billing dispute routed
#: to Accounts. Without it the queue is scoped to the teams the caller
#: actually staffs.
#:
#: **Team leadership is currently expressed as team membership.** There is no
#: lead/supervisor column on `tenant_team_members`, so a lead sees the queues
#: of the teams they are attached to -- which is the right answer when
#: leadership is modelled that way, and the wrong one for a supervisor who
#: oversees a team they do not staff. Recorded here rather than papered over
#: with a column nothing reads.
QUEUE_OVERSIGHT_PERMISSION = "tenant.conversations.view_all"


async def resolve_queue_team_scope(
    uow: object,
    *,
    tenant_id: UUID,
    user_id: UUID,
    permissions: frozenset[str],
) -> list[UUID] | None:
    """Which teams' conversations this caller may see.

    `None` means "every team", and is returned only for a caller holding the
    oversight permission. Everyone else gets the explicit list of teams they
    staff -- an empty list, meaning they see nothing, is a real and correct
    answer for someone who has been given inbox access but put on no team.

    Returning the scope rather than filtering rows afterwards is deliberate:
    the caller passes it into the query, so a conversation belonging to
    another team is never loaded, let alone filtered out.
    """
    if QUEUE_OVERSIGHT_PERMISSION in permissions:
        return None
    membership = await uow.tenant_memberships.get_by_tenant_and_user(  # type: ignore[attr-defined]
        tenant_id, user_id
    )
    if membership is None:
        # Inside a tenant-scoped unit of work with no membership row: nothing
        # to scope to, so nothing is visible. Fails closed.
        return []
    team_ids: list[UUID] = await uow.teams.list_team_ids_for_membership(  # type: ignore[attr-defined]
        tenant_id=tenant_id, membership_id=membership.id
    )
    return team_ids


@dataclass(frozen=True, slots=True)
class TeamOption:
    """A selectable choice, not a line of text the model wrote.

    Structured deliberately: a visitor picking "Admissions" must send an id the
    server can validate, not a string the server has to guess the meaning of.
    Team names come from the tenant's own rows, so nothing here is hard-coded.
    """

    id: str
    label: str
    description: str | None = None


@dataclass(frozen=True, slots=True)
class HandoffOffer:
    message: str
    teams: list[TeamOption]


async def available_teams(
    uow: AiResourceUnitOfWork, *, tenant_id: UUID
) -> list[TenantTeam]:
    return await uow.teams.list_for_tenant(tenant_id, active_only=True)


def build_offer(teams: list[TenantTeam]) -> HandoffOffer:
    """The "which team?" prompt, or an honest refusal.

    A tenant that has configured no teams gets a message saying transfer is
    unavailable rather than an empty button row -- an offer with nothing behind
    it is worse than no offer, because the visitor waits.
    """
    if not teams:
        return HandoffOffer(
            message=(
                "I'm not able to transfer you to a colleague from here. "
                "Please use the contact details on our website and someone "
                "will be able to help."
            ),
            teams=[],
        )
    return HandoffOffer(
        message="Of course. Which team would you like to speak with?",
        teams=[
            TeamOption(id=str(t.id), label=t.name, description=t.description)
            for t in teams
        ],
    )


@dataclass(frozen=True, slots=True)
class RequestHandoffCommand:
    """Routing a conversation to a team.

    `actor_user_id` is `None` for a visitor-initiated handoff on the public
    path -- a visitor is not a user, and inventing one to satisfy a signature
    would put a fake identity in the audit trail.
    """

    tenant_id: str
    conversation_id: str
    team_id: str | None
    reason: str | None
    initiated_by: HandoffInitiator
    actor_user_id: str | None = None
    #: Set by the caller that already knows the tenant permits handoff, so the
    #: check is not repeated inside a transaction that just did it.
    skip_policy_check: bool = False


class RequestHandoff:
    """Moves a conversation out of the AI's hands and into a team's queue.

    Optionally writes an AI summary as an internal comment first. **The summary
    is best-effort and the handoff is not**: a failure to generate a precis
    must never strand a visitor who asked for a person, so the summary is
    attempted inside its own try/except and its failure is logged rather than
    raised.
    """

    def __init__(
        self,
        uow_factory: AiResourceUowFactory,
        clock: Clock,
        events: ConversationEventPublisher | None = None,
        summariser: object | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._events = events
        self._summariser = summariser

    async def execute(self, command: RequestHandoffCommand) -> None:
        tenant_id = UUID(command.tenant_id)
        conversation_id = UUID(command.conversation_id)
        # The public path has no user; the platform's own id is not a person
        # and must not be recorded as the actor. `uuid4()` here is only the
        # RLS session's user id, which for a visitor path is meaningless by
        # design -- the tenant scope is what confines the transaction.
        actor_id = UUID(command.actor_user_id) if command.actor_user_id else uuid4()
        now = self._clock.now()

        async with self._uow_factory(actor_id, tenant_id) as uow:
            if not command.skip_policy_check:
                settings = await uow.chatbot_settings.get_for_tenant(tenant_id)
                if settings is not None and not settings.allow_human_handoff:
                    raise HandoffNotAvailableError(
                        "this organisation has not enabled transfers to a colleague"
                    )

            conversation = await uow.conversations.get_by_id(conversation_id)
            if conversation is None or conversation.tenant_id != tenant_id:
                raise ConversationNotFoundError(command.conversation_id)

            team_id: UUID | None = None
            if command.team_id:
                team = await uow.teams.get(
                    tenant_id=tenant_id, team_id=UUID(command.team_id)
                )
                # Validated against *this* tenant's teams. A team id from
                # another tenant is a 404, never a 403 -- it must not be
                # provable that it exists.
                if team is None or not team.is_active:
                    raise TeamNotFoundError(command.team_id or "")
                team_id = team.id

            await self._maybe_write_summary(
                uow,
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                team_id=team_id,
                now=now,
            )

            moved = await uow.handoff.route_to_team(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                team_id=team_id,
                reason=command.reason,
                initiated_by=command.initiated_by,
                now=now,
            )
            if not moved:
                # Already with an agent. Not an error for the visitor -- they
                # are getting the human they asked for -- so this returns
                # quietly rather than raising.
                return

            seq = await uow.conversation_messages.next_seq(conversation_id)
            await uow.conversation_messages.add_many(
                [
                    ConversationMessage(
                        id=uuid4(),
                        tenant_id=tenant_id,
                        conversation_id=conversation_id,
                        seq=seq,
                        role=MessageRole.SYSTEM_EVENT,
                        content=(
                            "Conversation transferred to a colleague."
                            if team_id is None
                            else "Conversation transferred to the selected team."
                        ),
                        created_at=now,
                    )
                ]
            )
            await uow.audit.record(
                actor_user_id=UUID(command.actor_user_id) if command.actor_user_id else None,
                effective_user_id=None,
                tenant_id=tenant_id,
                action="tenant.conversation.handed_off",
                resource_type="conversation",
                resource_id=conversation_id,
                result="success",
                metadata={
                    "team_id": str(team_id) if team_id else None,
                    "initiated_by": command.initiated_by.value,
                    "reason": command.reason,
                },
            )

        # Published *after* the transaction commits. Inside it, a subscriber
        # could be woken by an event describing a row that then rolls back --
        # an agent opening a conversation that does not exist.
        if self._events is not None and team_id is not None:
            await self._events.publish(
                tenant_id=tenant_id,
                event="conversation.unassigned",
                payload={
                    "conversation_id": str(conversation_id),
                    "team_id": str(team_id),
                    "handoff_at": now.isoformat(),
                },
            )

    async def _maybe_write_summary(
        self,
        uow: AiResourceUnitOfWork,
        *,
        tenant_id: UUID,
        conversation_id: UUID,
        team_id: UUID | None,
        now: object,
    ) -> None:
        """Writes the AI's precis as a staff-only internal comment.

        **Best-effort by construction.** Requirement: "if AI summary generation
        fails, handoff must still succeed" -- so every failure path here is
        swallowed and logged. The alternative is a visitor stuck in a
        conversation nobody owns because a summarising call timed out.
        """
        settings = await uow.chatbot_settings.get_for_tenant(tenant_id)
        if settings is None or not settings.add_ai_summary_as_internal_comment:
            return
        if self._summariser is None:
            return
        try:
            messages = await uow.conversation_messages.list_page(
                conversation_id, limit=40, offset=0
            )
            summary = await self._summariser.summarise(  # type: ignore[attr-defined]
                messages=messages, team_id=team_id
            )
            if not summary:
                return
            seq = await uow.conversation_messages.next_seq(conversation_id)
            await uow.conversation_messages.add_many(
                [
                    ConversationMessage(
                        id=uuid4(),
                        tenant_id=tenant_id,
                        conversation_id=conversation_id,
                        seq=seq,
                        # The privacy-critical bit. `INTERNAL_COMMENT` is
                        # filtered out of every visitor-facing read by
                        # `MessageRole.visible_to_visitor`, so this can never
                        # be delivered to the person it is about.
                        role=MessageRole.INTERNAL_COMMENT,
                        content=summary,
                        created_at=now,  # type: ignore[arg-type]
                    )
                ]
            )
        except Exception:
            logger.warning(
                "handoff summary could not be generated for conversation %s "
                "-- continuing with the handoff",
                conversation_id,
                exc_info=True,
            )


@dataclass(frozen=True, slots=True)
class ClaimConversationCommand:
    actor_user_id: str
    tenant_id: str
    conversation_id: str
    membership_id: str
    permissions: frozenset[str]


class ClaimConversation:
    """One agent takes an unassigned conversation.

    The race is settled by the repository's conditional UPDATE, not here --
    see `SqlConversationHandoffRepository.claim`. This use case's job is to
    turn "zero rows matched" into a 409 the losing agent's console can show,
    rather than a silent success that puts two people on one conversation.
    """

    def __init__(
        self,
        uow_factory: AiResourceUowFactory,
        clock: Clock,
        events: ConversationEventPublisher | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._events = events

    async def execute(self, command: ClaimConversationCommand) -> None:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        conversation_id = UUID(command.conversation_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id, tenant_id) as uow:
            if AGENT_PERMISSION not in command.permissions:
                raise PermissionDeniedError(AGENT_PERMISSION)

            won = await uow.handoff.claim(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                membership_id=UUID(command.membership_id),
                now=now,
            )
            if not won:
                # Zero rows means one of two different things, and they need
                # different answers. Found by driving this live: claiming a
                # conversation id that does not exist reported "another agent
                # has already picked this up", which is simply untrue and sends
                # an agent looking for a colleague who was never there.
                #
                # Disambiguated on the *failure* path only, so the happy path
                # is still a single statement and the race is still settled by
                # Postgres. A cross-tenant id is invisible under RLS, so it
                # reads as absent and answers 404 -- consistent with every
                # other route here, and it still cannot prove the row exists
                # somewhere else.
                existing = await uow.conversations.get_by_id(conversation_id)
                if existing is None or existing.tenant_id != tenant_id:
                    raise ConversationNotFoundError(command.conversation_id)
                raise ConversationAlreadyClaimedError(
                    "another agent has already picked up this conversation"
                )
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=actor_id,
                tenant_id=tenant_id,
                action="tenant.conversation.claimed",
                resource_type="conversation",
                resource_id=conversation_id,
                result="success",
                metadata={"membership_id": command.membership_id},
            )

        if self._events is not None:
            # Tells every other agent's inbox to drop it, so two people do not
            # stare at a conversation only one of them can have.
            await self._events.publish(
                tenant_id=tenant_id,
                event="conversation.claimed",
                payload={
                    "conversation_id": str(conversation_id),
                    "membership_id": command.membership_id,
                },
            )


@dataclass(frozen=True, slots=True)
class PostAgentMessageCommand:
    actor_user_id: str
    tenant_id: str
    conversation_id: str
    membership_id: str
    permissions: frozenset[str]
    content: str
    #: True writes a staff-only note instead of a reply to the visitor.
    internal: bool = False


class PostAgentMessage:
    """A human agent replies, or leaves an internal note.

    **Neither consumes AI quota.** The daily message counter is reserved only
    on the AI path; a support team answering tickets costs the platform no
    inference and must not be able to exhaust the chatbot's allowance by being
    busy.
    """

    def __init__(
        self,
        uow_factory: AiResourceUowFactory,
        clock: Clock,
        events: ConversationEventPublisher | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._events = events

    async def execute(self, command: PostAgentMessageCommand) -> None:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        conversation_id = UUID(command.conversation_id)
        now = self._clock.now()

        content = command.content.strip()
        if not content:
            raise ValueError("a message cannot be empty")

        async with self._uow_factory(actor_id, tenant_id) as uow:
            if AGENT_PERMISSION not in command.permissions:
                raise PermissionDeniedError(AGENT_PERMISSION)

            conversation = await uow.conversations.get_by_id(conversation_id)
            if conversation is None or conversation.tenant_id != tenant_id:
                raise ConversationNotFoundError(command.conversation_id)

            seq = await uow.conversation_messages.next_seq(conversation_id)
            await uow.conversation_messages.add_many(
                [
                    ConversationMessage(
                        id=uuid4(),
                        tenant_id=tenant_id,
                        conversation_id=conversation_id,
                        seq=seq,
                        role=(
                            MessageRole.INTERNAL_COMMENT
                            if command.internal
                            else MessageRole.AGENT
                        ),
                        content=content,
                        created_at=now,
                    )
                ]
            )
            if not command.internal:
                # An agent speaking is what makes the conversation
                # human-active, and that is what keeps the AI quiet. An
                # internal note deliberately does not: leaving oneself a
                # reminder is not taking over the conversation.
                await uow.handoff.set_state(
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    state=ConversationState.HUMAN_ACTIVE,
                    now=now,
                )

        if self._events is not None and not command.internal:
            await self._events.publish(
                tenant_id=tenant_id,
                event="conversation.agent_message",
                payload={"conversation_id": str(conversation_id)},
            )


@dataclass(frozen=True, slots=True)
class ReturnConversationToAiCommand:
    actor_user_id: str
    tenant_id: str
    conversation_id: str
    permissions: frozenset[str]


class ReturnConversationToAi:
    """The explicit, supported way to put the AI back in charge.

    Exists as its own action rather than falling out of some other event
    precisely because requirement 10 forbids the AI resuming *automatically*.
    Only a person deciding "the AI can take this from here" moves it back, and
    that decision is audited.
    """

    def __init__(self, uow_factory: AiResourceUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: ReturnConversationToAiCommand) -> None:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        conversation_id = UUID(command.conversation_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id, tenant_id) as uow:
            if AGENT_PERMISSION not in command.permissions:
                raise PermissionDeniedError(AGENT_PERMISSION)
            settings = await uow.chatbot_settings.get_for_tenant(tenant_id)
            if settings is not None and not settings.ai_chatbot_enabled:
                raise HandoffNotAvailableError(
                    "the AI assistant is switched off for this organisation"
                )
            moved = await uow.handoff.set_state(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                state=ConversationState.AI_ACTIVE,
                now=now,
                clear_assignment=True,
            )
            if not moved:
                raise ConversationNotFoundError(command.conversation_id)
            # Handing the thread back *is* "explicitly changing the mode
            # again", so a hold set earlier is released here. Leaving it set
            # would arm a trap: the next handoff on this conversation would
            # silently never time out, because of a decision an agent made
            # about a different part of the conversation hours before.
            await uow.handoff.set_ai_fallback_disabled(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                disabled=False,
                now=now,
            )
            # Both directions of the handover leave a marker in the thread.
            # Only the outbound one did, so a conversation that went AI ->
            # human -> AI read as though the colleague were still on it: the
            # transfer was recorded and the return was not.
            seq = await uow.conversation_messages.next_seq(conversation_id)
            await uow.conversation_messages.add_many(
                [
                    ConversationMessage(
                        id=uuid4(),
                        tenant_id=tenant_id,
                        conversation_id=conversation_id,
                        seq=seq,
                        role=MessageRole.SYSTEM_EVENT,
                        content="You're back with the assistant.",
                        created_at=now,
                    )
                ]
            )
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=actor_id,
                tenant_id=tenant_id,
                action="tenant.conversation.returned_to_ai",
                resource_type="conversation",
                resource_id=conversation_id,
                result="success",
            )


@dataclass(frozen=True, slots=True)
class SetConversationAiModeCommand:
    actor_user_id: str
    tenant_id: str
    conversation_id: str
    #: True = the AI may take this thread back when the tenant side goes quiet.
    #: False = this agent is holding it, whatever the timer says.
    ai_fallback_enabled: bool
    permissions: frozenset[str]


class SetConversationAiMode:
    """An agent taking the automatic fallback off (or back on) for one thread.

    **Deliberately per-conversation, not tenant-wide.** The organisation-level
    switch already exists on the chatbot settings screen, and it answers a
    different question: whether this tenant uses an AI at all. An agent mid-
    conversation is answering "should the assistant interrupt *this*", and
    making that decision by flipping a tenant-wide setting would silence the
    assistant for every other visitor on the site at the same moment.

    It does not change the conversation's state. A thread being held is still
    queued or assigned exactly as it was; the only thing that changes is
    whether `AdvanceHandoffFallback` is allowed to move it. Bundling a state
    change in would make "hold this" and "claim this" the same action, and an
    agent who has not claimed a conversation may still want it held for the
    colleague who has.
    """

    def __init__(self, uow_factory: AiResourceUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: SetConversationAiModeCommand) -> None:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        conversation_id = UUID(command.conversation_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id, tenant_id) as uow:
            if AGENT_PERMISSION not in command.permissions:
                raise PermissionDeniedError(AGENT_PERMISSION)
            updated = await uow.handoff.set_ai_fallback_disabled(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                disabled=not command.ai_fallback_enabled,
                now=now,
            )
            # Zero rows is "no such conversation here" -- including one in
            # another tenant, which RLS has already made invisible. Reported as
            # not-found rather than forbidden, so a caller cannot use this to
            # discover that a conversation id exists somewhere else.
            if not updated:
                raise ConversationNotFoundError(command.conversation_id)
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=actor_id,
                tenant_id=tenant_id,
                action=(
                    "tenant.conversation.ai_fallback_enabled"
                    if command.ai_fallback_enabled
                    else "tenant.conversation.ai_fallback_disabled"
                ),
                resource_type="conversation",
                resource_id=conversation_id,
                result="success",
            )


@dataclass(frozen=True, slots=True)
class SetAgentTypingCommand:
    actor_user_id: str
    tenant_id: str
    conversation_id: str
    typing: bool
    permissions: frozenset[str]


class SetAgentTyping:
    """A colleague is composing a reply, or has stopped.

    **One indicator per side, not per agent**, which is what stops two people
    working the same conversation producing two "is typing" lines. They share a
    key: whichever of them typed most recently keeps it alive, and it lapses
    when neither does. The visitor is told a colleague is replying; how many
    colleagues are looking at their question is not something to report to a
    stranger.

    Permission-gated like every other agent action -- an anonymous caller must
    not be able to make a widget claim a human is about to reply. Nothing is
    written to the thread and nothing is audited: an audit row per keystroke
    burst would drown the log that matters in noise about people thinking.
    """

    def __init__(
        self,
        uow_factory: AiResourceUowFactory,
        typing: TypingIndicatorStore,
    ) -> None:
        self._uow_factory = uow_factory
        self._typing = typing

    async def execute(self, command: SetAgentTypingCommand) -> None:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        conversation_id = UUID(command.conversation_id)

        async with self._uow_factory(actor_id, tenant_id) as uow:
            if AGENT_PERMISSION not in command.permissions:
                raise PermissionDeniedError(AGENT_PERMISSION)
            # Loaded under RLS, so another tenant's id is simply absent rather
            # than refused -- and a caller cannot use this to learn that a
            # conversation exists elsewhere.
            conversation = await uow.conversations.get_by_id(conversation_id)
            if conversation is None or conversation.tenant_id != tenant_id:
                raise ConversationNotFoundError(command.conversation_id)

        if command.typing:
            await self._typing.mark_typing(
                conversation_id=str(conversation_id), side="agent"
            )
        else:
            await self._typing.clear(conversation_id=str(conversation_id), side="agent")
