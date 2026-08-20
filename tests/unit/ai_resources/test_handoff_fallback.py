"""A handed-off conversation must not become a dead end.

The scenario these cover is the one the handoff feature could not survive: a
visitor asks for a person, the request is queued or claimed, and then nobody
on the tenant side says anything. Before this the visitor typed into silence
indefinitely, with the assistant deliberately held back.

Everything here is driven off **stored timestamps**, which is the property most
worth testing: a client-side timer would restart on every refresh and the
minute would never elapse for anyone who reloaded. So the tests move the clock
rather than sleeping, and assert on rows rather than on scheduled callbacks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from iam_platform.application.ai_resources.public_conversation import (
    UNAVAILABLE_NOTICE,
    WAITING_NOTICE,
    AdvanceHandoffFallback,
    AdvanceHandoffFallbackCommand,
)
from iam_platform.domain.ai_resources.entities import (
    ChatWidget,
    Conversation,
    ConversationMessage,
    ConversationState,
    MessageRole,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
ORIGIN = "https://help.acme.test"


class _MovableClock:
    def __init__(self, at: datetime = NOW) -> None:
        self.at = at

    def now(self) -> datetime:
        return self.at


def _widget(tenant_id: UUID) -> ChatWidget:
    return ChatWidget(
        id=uuid4(),
        tenant_id=tenant_id,
        knowledge_base_id=uuid4(),
        name="Help",
        public_key="wk_public",
        allowed_origins=[ORIGIN],
        created_by_membership_id=uuid4(),
        created_at=NOW,
        updated_at=NOW,
    )


@dataclass
class _FakeLookup:
    widget: ChatWidget

    async def find_by_public_key(self, public_key: str) -> ChatWidget | None:
        del public_key
        return self.widget

    async def find_by_widget_id(self, widget_id: UUID) -> ChatWidget | None:
        return self.widget if widget_id == self.widget.id else None


class _FakeConversations:
    def __init__(self, conversation: Conversation) -> None:
        self.conversation = conversation

    async def find_by_visitor_session(
        self, *, tenant_id: UUID, visitor_session_id: UUID
    ) -> Conversation | None:
        c = self.conversation
        if c.tenant_id == tenant_id and c.visitor_session_id == visitor_session_id:
            return c
        return None


class _FakeMessages:
    def __init__(self) -> None:
        self.rows: list[ConversationMessage] = []

    async def next_seq(self, conversation_id: UUID) -> int:
        return len([m for m in self.rows if m.conversation_id == conversation_id]) + 1

    async def list_after(
        self, *, conversation_id: UUID, after_seq: int
    ) -> list[ConversationMessage]:
        return [
            m
            for m in self.rows
            if m.conversation_id == conversation_id and m.seq > after_seq
        ]

    async def add_many(self, messages: list[ConversationMessage]) -> None:
        self.rows.extend(messages)


@dataclass
class _FakeSettings:
    ai_chatbot_enabled: bool = True


@dataclass
class _FakeChatbotSettings:
    settings: _FakeSettings | None = field(default_factory=_FakeSettings)

    async def get_for_tenant(self, tenant_id: UUID) -> _FakeSettings | None:
        del tenant_id
        return self.settings


class _FakeHandoff:
    """Only the two calls the fallback makes, with the *conditional* semantics
    of the real one: `set_state` reports whether it actually moved a row, which
    is what stops two overlapping polls both announcing the hand-back."""

    def __init__(self, conversation: Conversation, *, lose_race: bool = False) -> None:
        self.conversation = conversation
        self.state_calls = 0
        # Models the row having already been moved by a concurrent poll: the
        # real UPDATE reports zero rows changed, and this is the only way to
        # reproduce that from a sequential test.
        self.lose_race = lose_race

    async def set_state(
        self,
        *,
        tenant_id: UUID,
        conversation_id: UUID,
        state: ConversationState,
        now: datetime,
        clear_assignment: bool = False,
    ) -> bool:
        del tenant_id, now
        if self.lose_race:
            return False
        if self.conversation.id != conversation_id:
            return False
        if self.conversation.state is state:
            return False
        self.state_calls += 1
        self.conversation.state = state
        if clear_assignment:
            self.conversation.assigned_membership_id = None
            self.conversation.assigned_team_id = None
        return True


class _FakeUow:
    def __init__(
        self,
        conversations: _FakeConversations,
        messages: _FakeMessages,
        handoff: _FakeHandoff,
        chatbot_settings: _FakeChatbotSettings,
    ) -> None:
        self.conversations = conversations
        self.conversation_messages = messages
        self.handoff = handoff
        self.chatbot_settings = chatbot_settings

    async def __aenter__(self) -> _FakeUow:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class _FakeTyping:
    def __init__(self) -> None:
        self.state: dict[tuple[str, str], str] = {}

    async def who_is_typing(self, *, conversation_id: str, side: str) -> str | None:
        return self.state.get((conversation_id, side))


@dataclass
class _World:
    use_case: AdvanceHandoffFallback
    clock: _MovableClock
    conversation: Conversation
    messages: _FakeMessages
    handoff: _FakeHandoff
    command: AdvanceHandoffFallbackCommand
    widget: ChatWidget
    typing: _FakeTyping

    async def poll(self) -> None:
        await self.use_case.execute(self.command)

    def advance(self, seconds: int) -> None:
        self.clock.at = self.clock.at + timedelta(seconds=seconds)

    def system_events(self) -> list[str]:
        return [
            m.content for m in self.messages.rows if m.role is MessageRole.SYSTEM_EVENT
        ]

    def agent_says(self, text: str, *, at: datetime | None = None) -> None:
        self.messages.rows.append(
            ConversationMessage(
                id=uuid4(),
                tenant_id=self.conversation.tenant_id,
                conversation_id=self.conversation.id,
                seq=len(self.messages.rows) + 1,
                role=MessageRole.AGENT,
                content=text,
                created_at=at or self.clock.at,
            )
        )


def _world(
    *,
    state: ConversationState = ConversationState.UNASSIGNED,
    ai_enabled: bool = True,
    ai_fallback_disabled: bool = False,
    lose_race: bool = False,
    agent_typing: bool = False,
) -> _World:
    tenant_id = uuid4()
    widget = _widget(tenant_id)
    session_id = uuid4()
    conversation = Conversation(
        id=uuid4(),
        tenant_id=tenant_id,
        assistant_id=None,
        membership_id=None,
        visitor_session_id=session_id,
        widget_id=widget.id,
        state=state,
        handoff_at=NOW,
        ai_fallback_disabled=ai_fallback_disabled,
        created_at=NOW,
        updated_at=NOW,
    )
    clock = _MovableClock()
    messages = _FakeMessages()
    handoff = _FakeHandoff(conversation, lose_race=lose_race)
    settings = _FakeChatbotSettings(_FakeSettings(ai_chatbot_enabled=ai_enabled))
    typing = _FakeTyping()
    if agent_typing:
        typing.state[(str(conversation.id), "agent")] = "Sam"

    def factory(user_id: UUID, tid: UUID) -> _FakeUow:
        del user_id, tid
        return _FakeUow(_FakeConversations(conversation), messages, handoff, settings)

    return _World(
        use_case=AdvanceHandoffFallback(
            _FakeLookup(widget),  # type: ignore[arg-type]
            factory,  # type: ignore[arg-type]
            clock,  # type: ignore[arg-type]
            typing,  # type: ignore[arg-type]
        ),
        clock=clock,
        conversation=conversation,
        messages=messages,
        handoff=handoff,
        command=AdvanceHandoffFallbackCommand(
            widget_id=widget.id, session_id=session_id, session_origin=ORIGIN
        ),
        widget=widget,
        typing=typing,
    )


class TestWaitingNotice:
    async def test_nothing_is_said_before_the_threshold(self) -> None:
        world = _world()
        world.advance(20)
        await world.poll()
        assert world.system_events() == []

    async def test_the_visitor_is_reassured_once_the_wait_is_long_enough(self) -> None:
        world = _world()
        world.advance(35)
        await world.poll()
        assert world.system_events() == [WAITING_NOTICE]

    async def test_the_notice_is_not_repeated_on_every_poll(self) -> None:
        """The visitor polls every four seconds. Sending the notice each time
        would turn one reassurance into a wall of identical messages -- which
        is why "already sent" is a lookup against the stored thread rather than
        a flag in the request that made it."""
        world = _world()
        world.advance(35)
        for _ in range(5):
            await world.poll()
            world.advance(4)
        assert world.system_events().count(WAITING_NOTICE) == 1

    async def test_the_wait_is_not_switched_out_of_human_mode(self) -> None:
        world = _world()
        world.advance(35)
        await world.poll()
        assert world.conversation.state is not ConversationState.AI_ACTIVE


class TestReturnToAi:
    async def test_the_assistant_takes_over_after_the_deadline(self) -> None:
        world = _world()
        world.advance(130)
        await world.poll()
        assert world.conversation.state is ConversationState.AI_ACTIVE
        assert UNAVAILABLE_NOTICE in world.system_events()

    async def test_the_visitor_is_told_before_the_assistant_resumes(self) -> None:
        """Silently swapping who is answering would read as the colleague
        replying in a suspiciously robotic voice."""
        world = _world()
        world.advance(130)
        await world.poll()
        assert world.system_events()[-1] == UNAVAILABLE_NOTICE

    async def test_an_agent_reply_resets_the_countdown(self) -> None:
        """The cancellation the requirement asks for, with no timer to cancel:
        the deadline is measured from the last thing the tenant side did."""
        world = _world(state=ConversationState.HUMAN_ACTIVE)
        world.advance(100)
        world.agent_says("Hi, looking into it now.")
        world.advance(45)
        await world.poll()
        assert world.conversation.state is ConversationState.HUMAN_ACTIVE

    async def test_a_visitor_typing_does_not_reset_the_countdown(self) -> None:
        """`last_message_at` would have -- so a person waiting and asking
        "hello?" would keep postponing the very rescue they need."""
        world = _world()
        world.advance(100)
        world.messages.rows.append(
            ConversationMessage(
                id=uuid4(),
                tenant_id=world.conversation.tenant_id,
                conversation_id=world.conversation.id,
                seq=len(world.messages.rows) + 1,
                role=MessageRole.USER,
                content="hello? is anyone there?",
                created_at=world.clock.at,
            )
        )
        world.advance(45)
        await world.poll()
        assert world.conversation.state is ConversationState.AI_ACTIVE

    async def test_repeated_polls_announce_the_hand_back_once(self) -> None:
        """Two tabs, or one tab still polling after the deadline passed."""
        world = _world()
        world.advance(130)
        await world.poll()
        await world.poll()
        assert world.system_events().count(UNAVAILABLE_NOTICE) == 1
        assert world.handoff.state_calls == 1

    async def test_a_poll_that_loses_the_race_stays_silent(self) -> None:
        """Two polls can both read a still-handed-off row before either writes;
        the conditional UPDATE is what serialises them, and only the one that
        actually moved the row may speak. Without this the visitor would be
        told twice that the team is unavailable."""
        world = _world(lose_race=True)
        world.advance(130)
        await world.poll()
        assert UNAVAILABLE_NOTICE not in world.system_events()

    async def test_an_already_ai_conversation_is_left_alone(self) -> None:
        world = _world(state=ConversationState.AI_ACTIVE)
        world.advance(600)
        await world.poll()
        assert world.system_events() == []


class TestAnExplicitDecisionOutranksTheTimer:
    async def test_an_agent_holding_the_thread_keeps_it(self) -> None:
        """The timer rescues a visitor from a queue nobody is working. That is
        not what is happening when an agent has said they have it, and
        interrupting them is the one thing this must never do."""
        world = _world(ai_fallback_disabled=True)
        world.advance(600)
        await world.poll()
        assert world.conversation.state is ConversationState.UNASSIGNED
        assert UNAVAILABLE_NOTICE not in world.system_events()

    async def test_a_held_thread_still_reassures_the_visitor(self) -> None:
        """Holding a conversation is a promise to answer it, not permission to
        leave the person staring at nothing."""
        world = _world(ai_fallback_disabled=True)
        world.advance(35)
        await world.poll()
        assert world.system_events() == [WAITING_NOTICE]

    async def test_a_tenant_with_the_assistant_switched_off_keeps_the_thread(
        self,
    ) -> None:
        """Handing back to an AI that is not allowed to answer would swap
        silence from a colleague for silence from nothing at all."""
        world = _world(ai_enabled=False)
        world.advance(600)
        await world.poll()
        assert world.conversation.state is ConversationState.UNASSIGNED
        assert UNAVAILABLE_NOTICE not in world.system_events()


class TestPersistenceAcrossARefresh:
    async def test_reloading_does_not_restart_the_countdown(self) -> None:
        """The whole reason the elapsed time is computed from stored rows. A
        fresh use-case instance -- which is what every request gets -- must
        reach the same conclusion as one that had been polling all along."""
        world = _world()
        world.advance(130)
        # A brand-new instance, as if the visitor had just reloaded the page.
        reloaded = AdvanceHandoffFallback(
            _FakeLookup(world.widget),  # type: ignore[arg-type]
            world.use_case._uow_factory,  # type: ignore[arg-type]
            world.clock,  # type: ignore[arg-type]
            world.typing,  # type: ignore[arg-type]
        )
        await reloaded.execute(
            AdvanceHandoffFallbackCommand(
                widget_id=world.command.widget_id,
                session_id=world.command.session_id,
                session_origin=ORIGIN,
            )
        )
        assert world.conversation.state is ConversationState.AI_ACTIVE


class TestALiveConversationIsLeftAlone:
    """The two defects found by running a real conversation through it.

    Both came from treating "the agent has not spoken for a while" as "the
    agent is gone". In a claimed, active conversation that is just an ordinary
    pause -- someone reading, checking a record, or composing a careful reply.
    """

    async def test_a_claimed_conversation_is_never_told_the_team_is_being_found(
        self,
    ) -> None:
        """It appeared mid-conversation, thirty seconds after the colleague's
        last message. The visitor was already connected, so the message was
        simply false."""
        world = _world(state=ConversationState.HUMAN_ACTIVE)
        world.advance(45)
        await world.poll()
        assert WAITING_NOTICE not in world.system_events()

    async def test_an_unclaimed_conversation_still_gets_the_notice(self) -> None:
        """The case the notice exists for, kept working by the test above."""
        world = _world(state=ConversationState.UNASSIGNED)
        world.advance(45)
        await world.poll()
        assert world.system_events() == [WAITING_NOTICE]

    async def test_an_agent_mid_sentence_keeps_the_conversation(self) -> None:
        """Someone composing a careful reply for two minutes had it taken off
        them -- and the visitor's next message then went to the model instead
        of to the person who was halfway through answering."""
        world = _world(state=ConversationState.HUMAN_ACTIVE, agent_typing=True)
        world.advance(300)
        await world.poll()
        assert world.conversation.state is ConversationState.HUMAN_ACTIVE
        assert UNAVAILABLE_NOTICE not in world.system_events()

    async def test_an_agent_who_stopped_typing_and_left_still_loses_it(self) -> None:
        """The typing key lapses on its own, so a closed tab cannot hold a
        conversation hostage -- which is the whole reason it has a TTL."""
        world = _world(state=ConversationState.HUMAN_ACTIVE, agent_typing=False)
        world.advance(300)
        await world.poll()
        assert world.conversation.state is ConversationState.AI_ACTIVE


class TestALongThreadStaysReadable:
    """The defect that made a live conversation look dead to the agent.

    `list_page(limit=50, offset=0)` returns the *first* fifty turns, so once a
    conversation passed fifty every new message landed outside the window. The
    agent's console polled every four seconds and rendered the same opening
    fifty turns for ever, while the visitor's replies piled up unseen -- the
    conversation was working perfectly and looked broken from one side.

    Driven against the fake repository rather than the use case, because the
    property is the *query shape*: which turns come back, and in what order.
    """

    def _thread(self, count: int):  # type: ignore[no-untyped-def]
        from tests.unit.ai_resources.fakes import FakeConversationMessageRepository

        repo = FakeConversationMessageRepository()
        conversation_id = uuid4()
        repo.by_conversation[conversation_id] = [
            ConversationMessage(
                id=uuid4(),
                tenant_id=uuid4(),
                conversation_id=conversation_id,
                seq=i,
                role=MessageRole.USER,
                content=f"turn {i}",
                created_at=NOW,
            )
            for i in range(1, count + 1)
        ]
        return repo, conversation_id

    async def test_the_newest_turns_are_returned_not_the_oldest(self) -> None:
        repo, cid = self._thread(76)
        page = await repo.list_tail(conversation_id=cid, limit=50)
        assert [m.seq for m in page] == list(range(27, 77))

    async def test_the_page_is_oldest_first_for_rendering(self) -> None:
        """Selected newest-first because "the last N" cannot be expressed by an
        ascending LIMIT -- but handed back in reading order."""
        repo, cid = self._thread(76)
        page = await repo.list_tail(conversation_id=cid, limit=10)
        assert [m.seq for m in page] == sorted(m.seq for m in page)

    async def test_paging_upward_neither_skips_nor_repeats(self) -> None:
        repo, cid = self._thread(76)
        first = await repo.list_tail(conversation_id=cid, limit=10)
        older = await repo.list_tail(
            conversation_id=cid, limit=10, before_seq=first[0].seq
        )
        assert [m.seq for m in older] == list(range(57, 67))
        assert [m.seq for m in first] == list(range(67, 77))
        assert not {m.seq for m in older} & {m.seq for m in first}

    async def test_a_cursor_is_unmoved_by_turns_arriving_mid_read(self) -> None:
        """Why this pages by `seq` and not by offset. Three messages land
        between the two requests -- an offset would count from a position that
        has already moved and hand back turns the caller has already seen."""
        repo, cid = self._thread(76)
        first = await repo.list_tail(conversation_id=cid, limit=10)
        for extra in range(77, 80):
            repo.by_conversation[cid].append(
                ConversationMessage(
                    id=uuid4(),
                    tenant_id=uuid4(),
                    conversation_id=cid,
                    seq=extra,
                    role=MessageRole.USER,
                    content=f"turn {extra}",
                    created_at=NOW,
                )
            )
        older = await repo.list_tail(
            conversation_id=cid, limit=10, before_seq=first[0].seq
        )
        assert [m.seq for m in older] == list(range(57, 67))
