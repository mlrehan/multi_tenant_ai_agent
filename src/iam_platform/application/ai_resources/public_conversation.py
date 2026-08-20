"""The visitor's side of a handed-off conversation: reading replies, and answering.

Without this the handoff is a dead end. The transfer moves the conversation, the
agent is notified, the agent replies -- and the reply lands in
`conversation_messages` where the person who asked for help cannot see it. The
widget said "a colleague will reply here" and nothing ever arrived.

**Two rules shape both use cases, and neither is a convenience.**

*The visitor sees only what was written to them.* `MessageRole.visible_to_visitor`
is the single predicate, so `internal_comment` -- including the AI handoff
summary written *about* them -- can never be delivered. Filtering here rather
than in SQL keeps that rule in one place; a second WHERE clause somewhere else
is a second place for it to be forgotten.

*The conversation is found from the token's session, never from a request.* A
visitor names no conversation id, so there is no id to tamper with: the session
id in the signed token resolves to exactly one thread, in exactly one tenant.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from iam_platform.application.ai_resources.exceptions import (
    ConversationNotFoundError,
    QuestionTooLongError,
    WidgetUnavailableError,
)
from iam_platform.application.ai_resources.ports import (
    AiResourceUowFactory,
    ConversationEventPublisher,
    PublicWidgetLookup,
    TypingIndicatorStore,
)
from iam_platform.core.clock import Clock
from iam_platform.domain.ai_resources.entities import (
    ChatWidget,
    ConversationMessage,
    ConversationState,
    MessageRole,
)
from iam_platform.domain.ai_resources.guardrails import MAX_QUESTION_CHARS

#: Turns delivered per poll. A handed-off thread is a support conversation, not
#: a transcript archive, and an unbounded read would let one long-running
#: session return thousands of rows to an anonymous caller.
MAX_DELIVERED = 50

#: How long a visitor waits before the widget says something. Long enough that
#: an agent reading the thread is not talked over, short enough that silence
#: never reads as a broken chat.
WAITING_NOTICE_AFTER = timedelta(seconds=30)

#: How long before the assistant takes the conversation back. A queue nobody
#: works must not strand the person who asked for help -- being answered by an
#: AI beats being answered by nothing.
#:
#: Comfortably longer than the notice above, because it is the *irreversible*
#: half: a visitor told to hold on can still be picked up by an agent, whereas
#: taking the conversation back ends the wait for them. Two minutes is long
#: enough that a colleague who stepped away briefly still keeps the thread.
RETURN_TO_AI_AFTER = timedelta(minutes=2)

WAITING_NOTICE = (
    "We're working to connect you with a team member. "
    "Please stay with us for a moment."
)
UNAVAILABLE_NOTICE = (
    "Our team isn't available right now, so I'll carry on helping you myself."
)


@dataclass(frozen=True, slots=True)
class VisitorTurn:
    seq: int
    #: `visitor`, `ai`, or `agent` -- deliberately *not* the storage role. A
    #: visitor has no business knowing about `agent_message` or `system_event`
    #: internals; they need to know who is speaking.
    #:
    #: The AI is separated from a colleague because the widget renders the two
    #: from different places: an answer is streamed into the panel as it is
    #: generated, while a colleague's reply only ever arrives by poll. Collapsing
    #: both to `agent` meant the poll re-delivered answers already on screen and
    #: the visitor saw every AI reply twice.
    author: str
    content: str
    created_at: str


@dataclass(frozen=True, slots=True)
class ReadVisitorMessagesQuery:
    widget_id: UUID
    session_id: UUID
    session_origin: str
    after_seq: int = 0
    #: Set to read **history** instead of live turns: the newest `history_limit`
    #: turns, or the ones immediately before `before_seq`.
    #:
    #: Two modes on one query rather than a second endpoint, because they read
    #: the same rows under the same session check -- and a second public route
    #: is a second place to get that check wrong.
    history_limit: int | None = None
    before_seq: int | None = None


@dataclass(frozen=True, slots=True)
class VisitorView:
    """The turns, *and* who owns the thread right now.

    The ownership flag is the half that was missing. A handoff is a state the
    widget enters and must be able to leave: an agent pressing "Return to AI"
    flips `state` back to `ai_active` server-side, but nothing on the visitor's
    side could observe that, so the widget stayed in human mode for the rest of
    the session and posted every further message to the human leg -- which the
    server then refused, because no colleague was handling it any more. The
    visitor saw their messages accepted into the panel and never answered.

    Carried on the poll the widget already makes, rather than inferred from the
    system-event marker's text: a client that string-matched an English sentence
    to decide whether the AI is answering would break on the first rewording.
    """

    turns: list[VisitorTurn]
    #: True while the conversation is out of the AI's hands -- queued *or*
    #: being handled. What the widget uses to leave human mode when an agent
    #: hands the thread back.
    with_human: bool
    #: True only once a colleague has actually taken it (`assigned` /
    #: `human_active`), as opposed to merely sitting in the queue.
    #:
    #: The distinction exists because conversations are now durable. A visitor
    #: who asked for a person, was queued, and closed the tab used to get a
    #: fresh conversation on their next visit; they now resume the same one --
    #: and resuming *into human mode* left them typing at a colleague who had
    #: never picked it up, with the assistant silenced, for every future visit.
    #: A queue nobody works therefore became a permanently dead chatbot.
    #:
    #: So a queued thread keeps the AI answering while the visitor waits. The
    #: queue entry is untouched, and the moment an agent claims it this flips
    #: and the widget hands over.
    agent_engaged: bool
    #: A colleague is composing a reply right now. Ephemeral -- read from a
    #: short-lived cache key, never from the thread, because it is not
    #: something anybody said.
    #:
    #: Deliberately a bare boolean: *which* colleague, or how many, is not
    #: something to report to an anonymous visitor.
    agent_typing: bool = False
    #: Older turns exist above the page just returned. Only meaningful in
    #: history mode; the live poll is always reading forwards.
    #:
    #: Derived server-side rather than inferred from a short page: a page that
    #: happens to be exactly `history_limit` long is otherwise
    #: indistinguishable from the last one.
    has_more: bool = False


class ReadVisitorMessages:
    """Everything said to this visitor after `after_seq`.

    Polled rather than streamed. SSE would need a per-visitor subscription held
    open on an anonymous surface, which is a connection an unauthenticated
    caller can hold for free -- a much larger commitment than a short poll while
    a conversation is actively with a human. The widget polls only after a
    handoff, so an ordinary AI-only session opens no extra connections at all.
    """

    def __init__(
        self,
        lookup: PublicWidgetLookup,
        uow_factory: AiResourceUowFactory,
        typing: TypingIndicatorStore | None = None,
    ) -> None:
        self._lookup = lookup
        self._uow_factory = uow_factory
        # Optional so a deployment without the store takes the original code
        # path rather than "the new path with nothing in it" -- those look
        # identical today and diverge the moment the parameter grows a default.
        self._typing = typing

    async def execute(self, query: ReadVisitorMessagesQuery) -> VisitorView:
        widget = await _require_widget(
            self._lookup, query.widget_id, query.session_origin
        )
        async with self._uow_factory(uuid4(), widget.tenant_id) as uow:
            conversation = await uow.conversations.find_by_visitor_session(
                tenant_id=widget.tenant_id, visitor_session_id=query.session_id
            )
            if conversation is None:
                # No thread persisted yet -- an AI-only session. Not an error:
                # there is simply nothing a colleague has said.
                return VisitorView(turns=[], with_human=False, agent_engaged=False)
            conversation_id = conversation.id
            if query.history_limit is not None:
                # Reading backwards through the thread. Capped so an anonymous
                # caller cannot ask for the whole history in one request.
                messages = await uow.conversation_messages.list_tail(
                    conversation_id=conversation.id,
                    limit=min(query.history_limit, MAX_DELIVERED),
                    before_seq=query.before_seq,
                )
                has_more = bool(messages) and messages[0].seq > 1
            else:
                messages = await uow.conversation_messages.list_after(
                    conversation_id=conversation.id, after_seq=query.after_seq
                )
                has_more = False
            # Read from the row inside the same transaction as the turns, so a
            # poll cannot report messages from one moment and ownership from
            # another and leave the widget in a mode the server disagrees with.
            with_human = conversation.state is not ConversationState.AI_ACTIVE
            agent_engaged = conversation.state in (
                ConversationState.ASSIGNED,
                ConversationState.HUMAN_ACTIVE,
            )
        # Read outside the transaction: it is cache state with a lifetime of
        # seconds, and holding a database transaction open across a Redis round
        # trip would be paying transaction cost for something that is allowed
        # to be a moment stale.
        agent_typing = False
        if self._typing is not None:
            agent_typing = (
                await self._typing.who_is_typing(
                    conversation_id=str(conversation_id), side="agent"
                )
                is not None
            )
        return VisitorView(
            turns=[
                VisitorTurn(
                    seq=m.seq,
                    author=_author_for(m.role),
                    content=m.content,
                    created_at=m.created_at.isoformat(),
                )
                for m in messages
                if m.role.visible_to_visitor
            ][:MAX_DELIVERED],
            with_human=with_human,
            agent_engaged=agent_engaged,
            agent_typing=agent_typing,
            has_more=has_more,
        )


@dataclass(frozen=True, slots=True)
class SendVisitorMessageCommand:
    widget_id: UUID
    session_id: UUID
    session_origin: str
    content: str


class SendVisitorMessage:
    """The visitor replying to a human, after the AI has stepped aside.

    **Never reaches the AI, and never touches either AI quota.** This is
    human-to-human traffic on a conversation a colleague owns; charging it
    against the chatbot's message allowance would let a long support exchange
    exhaust the tenant's AI budget, and running it through the model would have
    the assistant talking over the agent -- the exact outcome
    `Conversation.ai_may_reply` exists to prevent.
    """

    def __init__(
        self,
        lookup: PublicWidgetLookup,
        uow_factory: AiResourceUowFactory,
        clock: Clock,
        events: ConversationEventPublisher | None = None,
    ) -> None:
        self._lookup = lookup
        self._uow_factory = uow_factory
        self._clock = clock
        self._events = events

    async def execute(self, command: SendVisitorMessageCommand) -> int:
        content = command.content.strip()
        if not content:
            raise QuestionTooLongError("a message cannot be empty")
        if len(content) > MAX_QUESTION_CHARS:
            raise QuestionTooLongError(
                f"a message may be at most {MAX_QUESTION_CHARS} characters"
            )

        widget = await _require_widget(
            self._lookup, command.widget_id, command.session_origin
        )
        now = self._clock.now()

        async with self._uow_factory(uuid4(), widget.tenant_id) as uow:
            conversation = await uow.conversations.find_by_visitor_session(
                tenant_id=widget.tenant_id, visitor_session_id=command.session_id
            )
            # Refused when the AI still owns the thread: this endpoint exists
            # for the human leg only, and accepting a message here otherwise
            # would silently bypass the guardrails and quota the ask path
            # applies.
            if conversation is None or conversation.state is ConversationState.AI_ACTIVE:
                raise ConversationNotFoundError("no colleague is handling this chat")

            seq = await uow.conversation_messages.next_seq(conversation.id)
            await uow.conversation_messages.add_many(
                [
                    ConversationMessage(
                        id=uuid4(),
                        tenant_id=widget.tenant_id,
                        conversation_id=conversation.id,
                        seq=seq,
                        role=MessageRole.USER,
                        content=content,
                        created_at=now,
                    )
                ]
            )
            conversation.record_turn(now=now)
            await uow.conversations.save(conversation)

        if self._events is not None:
            # Wakes the agent's console the same way a handoff does, so a
            # claimed conversation surfaces a new message without a refresh.
            await self._events.publish(
                tenant_id=widget.tenant_id,
                event="conversation.visitor_message",
                payload={"conversation_id": str(conversation.id)},
            )
        return seq


@dataclass(frozen=True, slots=True)
class AdvanceHandoffFallbackCommand:
    widget_id: UUID
    session_id: UUID
    session_origin: str


class AdvanceHandoffFallback:
    """Keeps a handed-off conversation from becoming a dead end.

    An agent can claim a thread, say one thing and walk away, or never claim it
    at all -- and `ReturnConversationToAi` is a *deliberate* action nobody is
    obliged to take. Without this the visitor sits in human mode indefinitely,
    typing into a queue, which is the worst outcome the handoff feature can
    produce: they asked for help and got silence.

    **Driven by stored timestamps, not by a timer.** The elapsed time is
    computed from the conversation's own rows on each poll, so closing the tab,
    reloading, or coming back tomorrow cannot restart the countdown -- a
    client-side timer would reset on every refresh and the minute would never
    elapse for anyone who reloaded. It also means no scheduler and no worker:
    the visitor's existing 4-second poll is the tick.

    The clock runs from the **last thing the tenant side did** -- the transfer,
    the agent's most recent message, or their keystrokes right now -- so a
    colleague who is actually working the conversation resets it simply by
    working it. That is the cancellation the requirement asks for, with no
    timer to cancel.
    """

    def __init__(
        self,
        lookup: PublicWidgetLookup,
        uow_factory: AiResourceUowFactory,
        clock: Clock,
        typing: TypingIndicatorStore | None = None,
    ) -> None:
        self._lookup = lookup
        self._uow_factory = uow_factory
        self._clock = clock
        # Someone mid-sentence is present, whatever the message timestamps say.
        # Optional so a deployment without the store keeps the original
        # behaviour rather than a silently weaker version of the new one.
        self._typing = typing

    async def execute(self, command: AdvanceHandoffFallbackCommand) -> None:
        widget = await _require_widget(
            self._lookup, command.widget_id, command.session_origin
        )
        now = self._clock.now()

        async with self._uow_factory(uuid4(), widget.tenant_id) as uow:
            conversation = await uow.conversations.find_by_visitor_session(
                tenant_id=widget.tenant_id, visitor_session_id=command.session_id
            )
            if conversation is None:
                return
            if conversation.state is ConversationState.AI_ACTIVE:
                return

            # Only the tail is needed: the events this decides on (the transfer
            # marker, an agent's reply, a notice already sent) are by definition
            # recent. Reading the whole thread would grow with the conversation
            # for no extra information.
            next_seq = await uow.conversation_messages.next_seq(conversation.id)
            recent = await uow.conversation_messages.list_after(
                conversation_id=conversation.id, after_seq=max(0, next_seq - 21)
            )

            since = _last_tenant_activity(recent) or conversation.handoff_at
            if since is None:
                return
            waited = now - since

            # A colleague literally mid-sentence is not an absent colleague.
            # Without this, an agent composing a careful reply for two minutes
            # would have the conversation taken off them by the timer -- and
            # the visitor's next message would go to the model instead of to
            # the person who was in the middle of answering them.
            if await self._agent_is_typing(conversation.id):
                return

            if waited >= RETURN_TO_AI_AFTER:
                await self._return_to_ai(uow, conversation, widget.tenant_id, now)
            elif (
                waited >= WAITING_NOTICE_AFTER
                # Only while nobody has taken it. Once an agent has claimed the
                # thread the visitor *is* connected, and telling them the team
                # is still being found is simply false -- it appeared in the
                # middle of a live conversation, thirty seconds after the
                # colleague's last message, which is an ordinary pause.
                and conversation.state is ConversationState.UNASSIGNED
                and not _notice_already_sent(recent, since)
            ):
                await _append_system_event(
                    uow, conversation.id, widget.tenant_id, WAITING_NOTICE, now
                )

    async def _agent_is_typing(self, conversation_id: UUID) -> bool:
        if self._typing is None:
            return False
        return (
            await self._typing.who_is_typing(
                conversation_id=str(conversation_id), side="agent"
            )
            is not None
        )

    async def _return_to_ai(
        self, uow: object, conversation: object, tenant_id: UUID, now: datetime
    ) -> None:
        """Hands the thread back, unless a person has said not to.

        Two independent holds, and both are decisions somebody made rather than
        states the system fell into:

        *The tenant has switched the assistant off.* Returning a conversation to
        an AI that is not allowed to answer would swap silence from a colleague
        for silence from nothing at all.

        *An agent is holding this conversation.* The timer exists to rescue a
        visitor from a queue nobody is working -- which is not what is happening
        when someone has explicitly said they have it. Overriding them would
        make the assistant interrupt a colleague mid-conversation, which is the
        one thing an agent working a difficult thread cannot have.

        Either way the thread stays where it is: still queued or assigned, still
        reachable by an agent, and the visitor has already had the waiting
        notice, so they are not sitting in unexplained silence.
        """
        if conversation.ai_fallback_disabled:  # type: ignore[attr-defined]
            return
        settings = await uow.chatbot_settings.get_for_tenant(tenant_id)  # type: ignore[attr-defined]
        if settings is not None and not settings.ai_chatbot_enabled:
            return

        moved = await uow.handoff.set_state(  # type: ignore[attr-defined]
            tenant_id=tenant_id,
            conversation_id=conversation.id,  # type: ignore[attr-defined]
            state=ConversationState.AI_ACTIVE,
            now=now,
            clear_assignment=True,
        )
        # Zero rows means another poll -- a second tab, or an overlapping
        # request -- got here first. The message is written only by whichever
        # call actually moved the row, so the visitor is told once.
        if not moved:
            return
        await _append_system_event(
            uow, conversation.id, tenant_id, UNAVAILABLE_NOTICE, now  # type: ignore[attr-defined]
        )


def _last_tenant_activity(recent: list[ConversationMessage]) -> datetime | None:
    """When the tenant side last did something the visitor could see.

    Deliberately not `last_message_at`: that moves when the *visitor* types,
    so a person waiting and asking "hello?" would keep resetting the very
    timeout that exists to rescue them.
    """
    stamps = [
        m.created_at
        for m in recent
        if m.role in (MessageRole.AGENT, MessageRole.SYSTEM_EVENT)
    ]
    return max(stamps) if stamps else None


def _notice_already_sent(recent: list[ConversationMessage], since: datetime) -> bool:
    """Whether the visitor has already been told someone is coming.

    Checked against the stored thread rather than a flag in memory, so it holds
    across a refresh and across two tabs polling at once -- and so the notice
    cannot be repeated every four seconds for the rest of the wait.
    """
    return any(
        m.role is MessageRole.SYSTEM_EVENT
        and m.content == WAITING_NOTICE
        and m.created_at >= since
        for m in recent
    )


async def _append_system_event(
    uow: object, conversation_id: UUID, tenant_id: UUID, content: str, now: datetime
) -> None:
    """A status line, not somebody speaking.

    `SYSTEM_EVENT` rather than `ASSISTANT`: this is the widget reporting on the
    conversation, and labelling it as the assistant would both misattribute it
    and -- since the widget skips AI-authored turns in its poll, having already
    streamed them -- mean the visitor never saw it.
    """
    seq = await uow.conversation_messages.next_seq(conversation_id)  # type: ignore[attr-defined]
    await uow.conversation_messages.add_many(  # type: ignore[attr-defined]
        [
            ConversationMessage(
                id=uuid4(),
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                seq=seq,
                role=MessageRole.SYSTEM_EVENT,
                content=content,
                created_at=now,
            )
        ]
    )


@dataclass(frozen=True, slots=True)
class SetVisitorTypingCommand:
    widget_id: UUID
    session_id: UUID
    session_origin: str
    #: False is sent explicitly on send and on an emptied box. The TTL would
    #: end the indicator anyway, several seconds later -- and one still showing
    #: underneath a message that has already arrived reads as a second message
    #: coming that never does.
    typing: bool


class SetVisitorTyping:
    """The visitor is composing something, or has stopped.

    **Deliberately not a message.** Nothing here is written to the thread: a
    typing indicator is a fact about the next few seconds, and putting it in
    `conversation_messages` would place it in the transcript, the retention
    sweep, the agent's audit view and the model's prompt -- four places it does
    not belong, for something that is wrong again moments later.

    The conversation is resolved from the token's session, exactly as every
    other visitor route does, so no id travels in the request that could be
    tampered with. A session with no thread yet simply does nothing: there is
    no colleague on the other end to tell.
    """

    def __init__(
        self,
        lookup: PublicWidgetLookup,
        uow_factory: AiResourceUowFactory,
        typing: TypingIndicatorStore,
    ) -> None:
        self._lookup = lookup
        self._uow_factory = uow_factory
        self._typing = typing

    async def execute(self, command: SetVisitorTypingCommand) -> None:
        widget = await _require_widget(
            self._lookup, command.widget_id, command.session_origin
        )
        async with self._uow_factory(uuid4(), widget.tenant_id) as uow:
            conversation = await uow.conversations.find_by_visitor_session(
                tenant_id=widget.tenant_id, visitor_session_id=command.session_id
            )
            if conversation is None:
                return
            conversation_id = str(conversation.id)

        if command.typing:
            await self._typing.mark_typing(
                conversation_id=conversation_id, side="visitor"
            )
        else:
            await self._typing.clear(conversation_id=conversation_id, side="visitor")


def _author_for(role: MessageRole) -> str:
    if role is MessageRole.USER:
        return "visitor"
    if role is MessageRole.ASSISTANT:
        return "ai"
    return "agent"


async def _require_widget(
    lookup: PublicWidgetLookup, widget_id: UUID, origin: str
) -> ChatWidget:
    widget = await lookup.find_by_widget_id(widget_id)
    if widget is None or not widget.is_active:
        raise WidgetUnavailableError("this chat widget is not available")
    if not widget.permits_origin(origin):
        raise WidgetUnavailableError("this chat widget is not permitted on this site")
    return widget
