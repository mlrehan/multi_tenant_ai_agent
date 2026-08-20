"""The visitor's half of human handoff: offering teams, and performing the move.

The agent's half lives in `handoff.py`. This is the surface an anonymous
stranger reaches, so it carries the same constraints the rest of `public_chat`
does -- the tenant is read off the *widget row*, never from anything the caller
sends, and the team id a visitor supplies is validated against that tenant's
own teams before it is used.

**Widget conversations are now persisted from the first question**, by
`AskWidget` via `visitor_conversation.py` -- so by the time a handoff happens
the row usually exists and already carries the exchange. This module creates
it only when the visitor escalated without asking anything first.

That is a reversal of the original design, which stored a widget chat *only*
on escalation to avoid keeping a great many strangers' conversations. What
changed is that keeping them now has an expiry date: every conversation falls
under `tenant_chatbot_settings.conversation_retention_days` (30 by default) and
is deleted by `purge_conversations.py`. Storage with a retention policy is a
different proposition from storage forever, and it is what makes the tenant's
own console able to show what their chatbot has been asked.

The memory carry-over below remains for the case it was written for: a session
whose turns are in Redis but whose row was only just created.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from iam_platform.application.ai_resources.exceptions import (
    HandoffNotAvailableError,
    TeamNotFoundError,
    WidgetUnavailableError,
)
from iam_platform.application.ai_resources.handoff import HandoffOffer, TeamOption, build_offer
from iam_platform.application.ai_resources.notify_agents import (
    NotifyAgentsCommand,
    NotifyAgentsOfHandoff,
)
from iam_platform.application.ai_resources.ports import (
    AiResourceUowFactory,
    ConversationEventPublisher,
    PublicWidgetLookup,
    WidgetMemoryStore,
)
from iam_platform.application.ai_resources.visitor_conversation import (
    ensure_visitor_conversation,
)
from iam_platform.core.clock import Clock
from iam_platform.domain.ai_resources.entities import (
    ChatWidget,
    Conversation,
    ConversationMessage,
    HandoffInitiator,
    MessageRole,
)

logger = logging.getLogger("iam_platform.application.ai_resources.public_handoff")


@dataclass(frozen=True, slots=True)
class WidgetHandoffOfferQuery:
    widget_id: UUID
    session_origin: str


@dataclass(frozen=True, slots=True)
class WidgetChatPolicy:
    """What this tenant permits for one widget conversation, in one read.

    Both switches come from the same row, so answering "may the AI reply?" and
    "may this be transferred?" separately would be two queries that can
    disagree with each other by a race.
    """

    #: The master switch. False must keep the visitor out of retrieval, the
    #: model, and both quotas -- not merely hide the AI's reply.
    ai_enabled: bool
    handoff_allowed: bool
    offer: HandoffOffer


class OfferWidgetHandoff:
    """Builds the "which team?" reply, or an honest refusal.

    Returns `None` when the tenant has switched handoff off -- distinct from an
    offer with no teams, which is what a tenant that *permits* handoff but has
    configured none gets. The first means "do not mention transfers at all";
    the second means "say plainly that transfer is unavailable". Collapsing
    them would either promise a transfer that cannot happen or hide a feature
    the tenant turned on.
    """

    def __init__(
        self,
        lookup: PublicWidgetLookup,
        uow_factory: AiResourceUowFactory,
    ) -> None:
        self._lookup = lookup
        self._uow_factory = uow_factory

    async def execute(self, query: WidgetHandoffOfferQuery) -> HandoffOffer | None:
        policy = await self.policy(query)
        return policy.offer if policy.handoff_allowed else None

    async def policy(self, query: WidgetHandoffOfferQuery) -> WidgetChatPolicy:
        """Both switches and the team list, from one read of the settings row."""
        widget = await self._require_widget(query.widget_id, query.session_origin)

        # A visitor is not a user. The id here only fills the RLS session's
        # `app.user_id`; the *tenant* scope is what confines every read, and it
        # comes from the widget row.
        async with self._uow_factory(uuid4(), widget.tenant_id) as uow:
            settings = await uow.chatbot_settings.get_for_tenant(widget.tenant_id)
            teams = await uow.teams.list_for_tenant(widget.tenant_id, active_only=True)
        # A tenant with no settings row has never opened the screen, so both
        # switches take their documented defaults rather than being read as
        # "off" -- which would silently disable every widget on the platform
        # that predates the settings table.
        return WidgetChatPolicy(
            ai_enabled=settings is None or settings.ai_chatbot_enabled,
            handoff_allowed=settings is None or settings.allow_human_handoff,
            offer=build_offer(teams),
        )

    async def _require_widget(self, widget_id: UUID, origin: str) -> ChatWidget:
        widget = await self._lookup.find_by_widget_id(widget_id)
        if widget is None or not widget.is_active:
            raise WidgetUnavailableError("this chat widget is not available")
        if not widget.permits_origin(origin):
            raise WidgetUnavailableError("this chat widget is not permitted on this site")
        return widget


@dataclass(frozen=True, slots=True)
class SelectHandoffTeamCommand:
    widget_id: UUID
    session_id: UUID
    session_origin: str
    team_id: UUID
    #: What the visitor last said, so the agent sees why they escalated.
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class HandoffResult:
    conversation_id: UUID
    team_name: str
    message: str
    #: The highest turn already in the conversation at the moment of transfer.
    #: The widget uses this to start its post-handoff polling from here rather
    #: than from 0 -- otherwise the first poll re-fetches every turn the
    #: visitor already saw live and renders them a second time.
    last_seq: int


class SelectHandoffTeam:
    """The visitor presses a team button and the conversation actually moves.

    **The row moves before the reassuring sentence is produced**, and the
    sentence is built from the result. The failure this prevents is the one the
    requirement calls out: the AI saying "I'm transferring you to Admissions"
    while `state` stays `ai_active`, so the visitor waits for a colleague nobody
    told.
    """

    def __init__(
        self,
        lookup: PublicWidgetLookup,
        uow_factory: AiResourceUowFactory,
        clock: Clock,
        memory: WidgetMemoryStore | None = None,
        events: ConversationEventPublisher | None = None,
        summariser: object | None = None,
        notifier: NotifyAgentsOfHandoff | None = None,
    ) -> None:
        self._lookup = lookup
        self._uow_factory = uow_factory
        self._clock = clock
        self._memory = memory
        self._events = events
        self._summariser = summariser
        # Optional so every existing construction site keeps working. Without
        # it the handoff behaves exactly as before push notifications existed
        # -- not "with an empty notifier", which looks the same today and
        # diverges the moment this grows a default.
        self._notifier = notifier

    async def execute(self, command: SelectHandoffTeamCommand) -> HandoffResult:
        widget = await self._require_widget(command)
        now = self._clock.now()
        transcript = (
            await self._memory.recent(command.session_id) if self._memory else []
        )
        # **The escalating question is part of the thread even when memory is
        # empty.** Session memory is written by `AskWidget`, and the paths that
        # reach a handoff *bypass* it -- pressing "Speak to a person" straight
        # away, or any question at all once the tenant has switched the AI off.
        # Without this the agent inherited a conversation with zero messages,
        # which is precisely the "bare 'visitor wants help'" this file's own
        # docstring says the carry-over exists to prevent. Live rows with 0
        # messages are how it was found.
        reason = (command.reason or "").strip()
        if reason and not any(role == "user" for role, _ in transcript):
            transcript = [*transcript, ("user", reason)]

        async with self._uow_factory(uuid4(), widget.tenant_id) as uow:
            settings = await uow.chatbot_settings.get_for_tenant(widget.tenant_id)
            if settings is not None and not settings.allow_human_handoff:
                raise HandoffNotAvailableError(
                    "this organisation has not enabled transfers to a colleague"
                )

            # Validated against *this* tenant's teams. A team id from another
            # tenant is invisible under RLS and answers "not found" -- it must
            # not be provable that it exists.
            team = await uow.teams.get(
                tenant_id=widget.tenant_id, team_id=command.team_id
            )
            if team is None or not team.is_active:
                raise TeamNotFoundError(str(command.team_id))

            conversation = await self._ensure_conversation(
                uow, widget=widget, session_id=command.session_id, now=now
            )
            await self._write_transcript(
                uow,
                tenant_id=widget.tenant_id,
                conversation_id=conversation.id,
                transcript=transcript,
                now=now,
            )
            await self._write_handoff_reason(
                uow,
                tenant_id=widget.tenant_id,
                conversation_id=conversation.id,
                reason=reason,
                now=now,
            )
            await self._maybe_summarise(
                uow,
                tenant_id=widget.tenant_id,
                conversation_id=conversation.id,
                transcript=transcript,
                team_name=team.name,
                enabled=bool(settings and settings.add_ai_summary_as_internal_comment),
                now=now,
            )

            moved = await uow.handoff.route_to_team(
                tenant_id=widget.tenant_id,
                conversation_id=conversation.id,
                team_id=team.id,
                reason=command.reason,
                initiated_by=HandoffInitiator.VISITOR,
                now=now,
            )
            if not moved:
                # Already with an agent -- the visitor is getting the human they
                # asked for, so this is not an error to them.
                logger.info(
                    "conversation %s was already claimed; leaving it be",
                    conversation.id,
                )
            else:
                # The agent-initiated handoff (`RequestHandoff` in
                # handoff.py) leaves a `SYSTEM_EVENT` marker on every
                # transfer; this, the visitor-initiated path, wrote none --
                # so a thread that started by AI, was escalated by the
                # visitor, and continued with a human had no line in it
                # marking where the handover actually happened.
                seq = await uow.conversation_messages.next_seq(conversation.id)
                await uow.conversation_messages.add_many(
                    [
                        ConversationMessage(
                            id=uuid4(),
                            tenant_id=widget.tenant_id,
                            conversation_id=conversation.id,
                            seq=seq,
                            role=MessageRole.SYSTEM_EVENT,
                            content="Conversation transferred to the selected team.",
                            created_at=now,
                        )
                    ]
                )

            # Read after every write in this transaction, so the widget's
            # post-handoff polling starts exactly where the visitor's own view
            # of the conversation leaves off -- nothing already shown gets
            # fetched and rendered again.
            last_seq = await uow.conversation_messages.next_seq(conversation.id) - 1

        # Published after commit: a subscriber woken inside the transaction
        # could open a conversation that then rolled back.
        if self._events is not None:
            await self._events.publish(
                tenant_id=widget.tenant_id,
                event="conversation.unassigned",
                payload={
                    "conversation_id": str(conversation.id),
                    "team_id": str(team.id),
                    "handoff_at": now.isoformat(),
                },
            )

        if self._notifier is not None:
            # Reaches agents whose console is closed, which SSE cannot. After
            # the SSE publish, not instead of it: an agent with the tab open
            # should see the queue update immediately rather than wait on a
            # round trip to Apple or Google.
            #
            # Wrapped because the transfer has already committed. A push
            # service having a bad minute must not turn a completed handoff
            # into an error for a visitor who is already waiting.
            try:
                await self._notifier.execute(
                    NotifyAgentsCommand(
                        tenant_id=widget.tenant_id,
                        team_id=team.id,
                        team_name=team.name,
                    )
                )
            except Exception:
                logger.exception(
                    "could not send handoff push notifications for tenant %s",
                    widget.tenant_id,
                )

        return HandoffResult(
            conversation_id=conversation.id,
            team_name=team.name,
            message=(
                f"Thanks. I'm transferring your conversation to the {team.name} "
                "team now. Someone will pick this up as soon as they can."
            ),
            last_seq=last_seq,
        )

    # -- helpers -------------------------------------------------------------

    async def _require_widget(self, command: SelectHandoffTeamCommand) -> ChatWidget:
        widget = await self._lookup.find_by_widget_id(command.widget_id)
        if widget is None or not widget.is_active:
            raise WidgetUnavailableError("this chat widget is not available")
        if not widget.permits_origin(command.session_origin):
            raise WidgetUnavailableError("this chat widget is not permitted on this site")
        return widget

    async def _ensure_conversation(
        self, uow: object, *, widget: ChatWidget, session_id: UUID, now: datetime
    ) -> Conversation:
        """One conversation per widget session.

        Usually already created by `AskWidget` on the visitor's first question;
        created here when the visitor escalated without asking anything. Either
        way it comes from `ensure_visitor_conversation`, so both paths produce
        the same row -- the `exactly_one_owner` CHECK's shape, with
        `membership_id` null and the session id carried.
        """
        return await ensure_visitor_conversation(
            uow, widget=widget, session_id=session_id, now=now
        )

    async def _write_transcript(
        self,
        uow: object,
        *,
        tenant_id: UUID,
        conversation_id: UUID,
        transcript: list[tuple[str, str]],
        now: datetime,
    ) -> None:
        """Bootstraps a conversation that has no turns in Postgres yet.

        Only relevant when the visitor escalated without `AskWidget` ever
        running -- pressing "Speak to a person" straight away, or the AI being
        switched off -- because every ordinary question is now persisted live
        as it is asked. `next_seq` starting above 1 means the row already has
        turns, whether from that live persistence or an earlier handoff on
        this same session, and copying the Redis-memory transcript again would
        duplicate every one of them. `_write_handoff_reason` below is what
        covers the far more common case: a conversation that already has
        turns, but not the one that triggered *this* handoff.
        """
        if not transcript:
            return
        seq = await uow.conversation_messages.next_seq(conversation_id)  # type: ignore[attr-defined]
        if seq > 1:
            return
        messages = [
            ConversationMessage(
                id=uuid4(),
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                seq=seq + i,
                role=MessageRole.USER if role == "user" else MessageRole.ASSISTANT,
                content=content,
                created_at=now,
            )
            for i, (role, content) in enumerate(transcript)
        ]
        await uow.conversation_messages.add_many(messages)  # type: ignore[attr-defined]

    async def _write_handoff_reason(
        self,
        uow: object,
        *,
        tenant_id: UUID,
        conversation_id: UUID,
        reason: str,
        now: datetime,
    ) -> None:
        """The words the visitor actually used to ask for a person.

        **Every path that reaches a handoff bypasses `AskWidget`'s own
        persistence.** `wants_a_human()` intercepts the question in the
        router before it is ever answered, and the AI-switched-off path never
        calls `AskWidget` at all. So once a conversation already has turns --
        the ordinary case, since a visitor usually asks something before
        escalating -- the message that triggered *this* handoff is otherwise
        nowhere in `conversation_messages`: the agent inherits everything
        asked before the escalation and nothing of the escalation itself.
        Found by driving a real handoff end to end and reading the row back,
        not by inspection: the console showed a thread ending on an old
        answer, with no sign the visitor had ever asked for a person.
        """
        if not reason:
            return
        seq = await uow.conversation_messages.next_seq(conversation_id)  # type: ignore[attr-defined]
        if seq > 1:
            # Might already be the last thing `_write_transcript` wrote (the
            # bootstrap case, where `reason` was appended to `transcript`
            # before this function ever runs) -- compare rather than assume,
            # so this stays correct however the two calls end up interleaving.
            tail = await uow.conversation_messages.list_after(  # type: ignore[attr-defined]
                conversation_id=conversation_id, after_seq=seq - 2
            )
            if tail and tail[-1].role == MessageRole.USER and tail[-1].content == reason:
                return
        await uow.conversation_messages.add_many(  # type: ignore[attr-defined]
            [
                ConversationMessage(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    seq=seq,
                    role=MessageRole.USER,
                    content=reason,
                    created_at=now,
                )
            ]
        )

    async def _maybe_summarise(
        self,
        uow: object,
        *,
        tenant_id: UUID,
        conversation_id: UUID,
        transcript: list[tuple[str, str]],
        team_name: str,
        enabled: bool,
        now: datetime,
    ) -> None:
        """Writes a staff-only precis, if the tenant asked for one.

        **Best-effort by construction.** The requirement is explicit: if summary
        generation fails, the handoff must still succeed. So every failure here
        is swallowed and logged -- a visitor must never be stranded because a
        summarising call timed out.
        """
        if not enabled or not transcript:
            return
        try:
            summary = _extractive_summary(transcript, team_name)
            if not summary:
                return
            seq = await uow.conversation_messages.next_seq(conversation_id)  # type: ignore[attr-defined]
            await uow.conversation_messages.add_many(  # type: ignore[attr-defined]
                [
                    ConversationMessage(
                        id=uuid4(),
                        tenant_id=tenant_id,
                        conversation_id=conversation_id,
                        seq=seq,
                        # Staff-only by construction: `visible_to_visitor` is
                        # false for this role, and every visitor-facing read
                        # filters on that one predicate.
                        role=MessageRole.INTERNAL_COMMENT,
                        content=summary,
                        created_at=now,
                    )
                ]
            )
        except Exception:
            logger.warning(
                "handoff summary could not be written for conversation %s "
                "-- continuing with the handoff",
                conversation_id,
                exc_info=True,
            )


def _extractive_summary(transcript: list[tuple[str, str]], team_name: str) -> str:
    """Assembled from what was actually said, not written by the model.

    Same reasoning as conversation compaction: a second paid call on the
    handoff path is latency a waiting visitor feels, and asking the model to
    summarise gives a poisoned earlier turn a chance to rewrite the record a
    colleague is about to read. Extracting is dull and cannot be steered.
    """
    asked = [c for r, c in transcript if r == "user"]
    answered = [c for r, c in transcript if r == "assistant"]
    lines = [f"**AI handoff summary** — routed to {team_name}.", ""]
    if asked:
        lines.append(f"- Visitor asked: {asked[0].strip()[:300]}")
        if len(asked) > 1:
            lines.append(f"- Most recent question: {asked[-1].strip()[:300]}")
    if answered:
        lines.append(f"- The assistant last replied: {answered[-1].strip()[:300]}")
    lines.append(f"- Turns before transfer: {len(transcript)}")
    lines.append("- Unresolved: the visitor asked for a person after this exchange.")
    return "\n".join(lines)


__all__ = [
    "HandoffOffer",
    "HandoffResult",
    "OfferWidgetHandoff",
    "SelectHandoffTeam",
    "SelectHandoffTeamCommand",
    "TeamOption",
    "WidgetHandoffOfferQuery",
]
