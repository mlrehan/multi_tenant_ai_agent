"""Typing indicators: ephemeral, two-sided, and never part of the transcript.

The property worth guarding is not that the flag round-trips -- that is one
Redis key. It is that a typing indicator **stays out of the conversation**: no
message row, no audit entry, nothing that reaches the retention sweep, the
agent's audit view, or the model's prompt. It is a fact about the next few
seconds, and every place that stores turns would keep it long after it stopped
being true.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from iam_platform.application.ai_resources.exceptions import PermissionDeniedError
from iam_platform.application.ai_resources.handoff import (
    SetAgentTyping,
    SetAgentTypingCommand,
)
from iam_platform.application.ai_resources.public_conversation import (
    ReadVisitorMessages,
    ReadVisitorMessagesQuery,
    SetVisitorTyping,
    SetVisitorTypingCommand,
)
from iam_platform.domain.ai_resources.entities import (
    ChatWidget,
    Conversation,
    ConversationState,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
ORIGIN = "https://help.acme.test"
AGENT_PERMISSION = frozenset({"tenant.conversations.view"})


class _FakeTyping:
    """The store, minus Redis. Records calls as well as state, so a test can
    tell "cleared" from "never set" -- the two look identical from a read."""

    def __init__(self) -> None:
        self.state: dict[tuple[str, str], str] = {}
        self.calls: list[tuple[str, str, bool]] = []

    async def mark_typing(
        self, *, conversation_id: str, side: str, display_name: str = ""
    ) -> None:
        self.state[(conversation_id, side)] = display_name or side
        self.calls.append((conversation_id, side, True))

    async def clear(self, *, conversation_id: str, side: str) -> None:
        self.state.pop((conversation_id, side), None)
        self.calls.append((conversation_id, side, False))

    async def who_is_typing(self, *, conversation_id: str, side: str) -> str | None:
        return self.state.get((conversation_id, side))


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
    def __init__(self, conversation: Conversation | None) -> None:
        self.conversation = conversation

    async def find_by_visitor_session(
        self, *, tenant_id: UUID, visitor_session_id: UUID
    ) -> Conversation | None:
        c = self.conversation
        if c and c.tenant_id == tenant_id and c.visitor_session_id == visitor_session_id:
            return c
        return None

    async def get_by_id(self, conversation_id: UUID) -> Conversation | None:
        c = self.conversation
        return c if c and c.id == conversation_id else None


class _FakeMessages:
    rows: list[object] = []

    async def list_after(self, *, conversation_id: UUID, after_seq: int) -> list[object]:
        del conversation_id, after_seq
        return []

    async def list_tail(
        self, *, conversation_id: UUID, limit: int, before_seq: int | None = None
    ) -> list[object]:
        del conversation_id, limit, before_seq
        return []


class _FakeAudit:
    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []

    async def record(self, **kwargs: object) -> None:
        self.records.append(kwargs)


class _FakeUow:
    def __init__(self, conversation: Conversation | None) -> None:
        self.conversations = _FakeConversations(conversation)
        self.conversation_messages = _FakeMessages()
        self.audit = _FakeAudit()

    async def __aenter__(self) -> _FakeUow:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


def _conversation(tenant_id: UUID, session_id: UUID) -> Conversation:
    return Conversation(
        id=uuid4(),
        tenant_id=tenant_id,
        assistant_id=None,
        membership_id=None,
        visitor_session_id=session_id,
        widget_id=None,
        state=ConversationState.ASSIGNED,
        created_at=NOW,
        updated_at=NOW,
    )


@dataclass
class _World:
    widget: ChatWidget
    session_id: UUID
    conversation: Conversation | None
    typing: _FakeTyping
    uow: _FakeUow

    def factory(self):  # type: ignore[no-untyped-def]
        def make(user_id: UUID, tenant_id: UUID) -> _FakeUow:
            del user_id, tenant_id
            return self.uow

        return make


def _world(*, with_conversation: bool = True) -> _World:
    tenant_id = uuid4()
    widget = _widget(tenant_id)
    session_id = uuid4()
    conversation = _conversation(tenant_id, session_id) if with_conversation else None
    return _World(widget, session_id, conversation, _FakeTyping(), _FakeUow(conversation))


class TestTheVisitorSide:
    async def test_typing_is_recorded_against_their_own_thread(self) -> None:
        w = _world()
        assert w.conversation is not None
        await SetVisitorTyping(
            _FakeLookup(w.widget),  # type: ignore[arg-type]
            w.factory(),  # type: ignore[arg-type]
            w.typing,  # type: ignore[arg-type]
        ).execute(
            SetVisitorTypingCommand(
                widget_id=w.widget.id,
                session_id=w.session_id,
                session_origin=ORIGIN,
                typing=True,
            )
        )
        assert w.typing.state == {(str(w.conversation.id), "visitor"): "visitor"}

    async def test_stopping_is_sent_explicitly_rather_than_left_to_expire(self) -> None:
        """An indicator still showing under a message that has already arrived
        reads as a second message coming that never does."""
        w = _world()
        assert w.conversation is not None
        use_case = SetVisitorTyping(
            _FakeLookup(w.widget),  # type: ignore[arg-type]
            w.factory(),  # type: ignore[arg-type]
            w.typing,  # type: ignore[arg-type]
        )
        base = {
            "widget_id": w.widget.id,
            "session_id": w.session_id,
            "session_origin": ORIGIN,
        }
        await use_case.execute(SetVisitorTypingCommand(**base, typing=True))  # type: ignore[arg-type]
        await use_case.execute(SetVisitorTypingCommand(**base, typing=False))  # type: ignore[arg-type]
        assert w.typing.state == {}
        assert w.typing.calls[-1] == (str(w.conversation.id), "visitor", False)

    async def test_a_session_with_no_thread_does_nothing(self) -> None:
        """There is no colleague on the other end to tell."""
        w = _world(with_conversation=False)
        await SetVisitorTyping(
            _FakeLookup(w.widget),  # type: ignore[arg-type]
            w.factory(),  # type: ignore[arg-type]
            w.typing,  # type: ignore[arg-type]
        ).execute(
            SetVisitorTypingCommand(
                widget_id=w.widget.id,
                session_id=w.session_id,
                session_origin=ORIGIN,
                typing=True,
            )
        )
        assert w.typing.calls == []

    async def test_the_visitor_is_told_a_colleague_is_composing(self) -> None:
        w = _world()
        assert w.conversation is not None
        await w.typing.mark_typing(
            conversation_id=str(w.conversation.id), side="agent", display_name="Sam"
        )
        view = await ReadVisitorMessages(
            _FakeLookup(w.widget),  # type: ignore[arg-type]
            w.factory(),  # type: ignore[arg-type]
            w.typing,  # type: ignore[arg-type]
        ).execute(
            ReadVisitorMessagesQuery(
                widget_id=w.widget.id,
                session_id=w.session_id,
                session_origin=ORIGIN,
            )
        )
        assert view.agent_typing is True

    async def test_a_deployment_without_the_store_reports_nobody_typing(self) -> None:
        """The store is optional so an unwired deployment takes the original
        path, rather than the new one with nothing in it."""
        w = _world()
        view = await ReadVisitorMessages(
            _FakeLookup(w.widget),  # type: ignore[arg-type]
            w.factory(),  # type: ignore[arg-type]
        ).execute(
            ReadVisitorMessagesQuery(
                widget_id=w.widget.id,
                session_id=w.session_id,
                session_origin=ORIGIN,
            )
        )
        assert view.agent_typing is False


class TestTheAgentSide:
    async def test_an_agent_typing_is_recorded_on_the_tenant_side_key(self) -> None:
        w = _world()
        assert w.conversation is not None
        await SetAgentTyping(w.factory(), w.typing).execute(  # type: ignore[arg-type]
            SetAgentTypingCommand(
                actor_user_id=str(uuid4()),
                tenant_id=str(w.widget.tenant_id),
                conversation_id=str(w.conversation.id),
                typing=True,
                permissions=AGENT_PERMISSION,
            )
        )
        assert w.typing.state == {(str(w.conversation.id), "agent"): "agent"}

    async def test_two_agents_typing_produce_one_indicator(self) -> None:
        """The requirement the shared key exists for. Two people working the
        same conversation must not make the visitor see two "is typing" lines
        -- and how many colleagues are reading their question is not something
        to report to a stranger anyway."""
        w = _world()
        assert w.conversation is not None
        use_case = SetAgentTyping(w.factory(), w.typing)  # type: ignore[arg-type]
        for _ in range(2):
            await use_case.execute(
                SetAgentTypingCommand(
                    actor_user_id=str(uuid4()),
                    tenant_id=str(w.widget.tenant_id),
                    conversation_id=str(w.conversation.id),
                    typing=True,
                    permissions=AGENT_PERMISSION,
                )
            )
        assert len(w.typing.state) == 1

    async def test_an_uninvited_caller_cannot_claim_a_human_is_replying(self) -> None:
        w = _world()
        assert w.conversation is not None
        with pytest.raises(PermissionDeniedError):
            await SetAgentTyping(w.factory(), w.typing).execute(  # type: ignore[arg-type]
                SetAgentTypingCommand(
                    actor_user_id=str(uuid4()),
                    tenant_id=str(w.widget.tenant_id),
                    conversation_id=str(w.conversation.id),
                    typing=True,
                    permissions=frozenset(),
                )
            )
        assert w.typing.state == {}


class TestItIsNeverPartOfTheTranscript:
    async def test_no_message_row_and_no_audit_entry_is_written(self) -> None:
        """The whole reason this lives in a cache and not the thread. A row
        here would reach the transcript, the retention sweep, the agent's audit
        view and the model's prompt -- for something that is wrong again in
        three seconds."""
        w = _world()
        assert w.conversation is not None
        await SetAgentTyping(w.factory(), w.typing).execute(  # type: ignore[arg-type]
            SetAgentTypingCommand(
                actor_user_id=str(uuid4()),
                tenant_id=str(w.widget.tenant_id),
                conversation_id=str(w.conversation.id),
                typing=True,
                permissions=AGENT_PERMISSION,
            )
        )
        await SetVisitorTyping(
            _FakeLookup(w.widget),  # type: ignore[arg-type]
            w.factory(),  # type: ignore[arg-type]
            w.typing,  # type: ignore[arg-type]
        ).execute(
            SetVisitorTypingCommand(
                widget_id=w.widget.id,
                session_id=w.session_id,
                session_origin=ORIGIN,
                typing=True,
            )
        )
        assert w.uow.conversation_messages.rows == []
        assert w.uow.audit.records == []
