"""A returning visitor gets the end of their conversation, not all of it.

The widget used to restore the **whole** thread on open. That was fine for a
first conversation and quietly worse on every visit after: sessions are durable
now, so a regular would have every turn they had ever exchanged rendered before
the panel could open. History is therefore paged -- newest page first, older
ones fetched as they scroll up.

Both directions are served by the same read on purpose. A second public route
would be a second place to get the session check wrong, and these are the rows
an anonymous caller is least entitled to read loosely.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from iam_platform.application.ai_resources.public_conversation import (
    ReadVisitorMessages,
    ReadVisitorMessagesQuery,
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
    def __init__(self, rows: list[ConversationMessage]) -> None:
        self.rows = rows

    async def list_after(
        self, *, conversation_id: UUID, after_seq: int
    ) -> list[ConversationMessage]:
        del conversation_id
        return [m for m in self.rows if m.seq > after_seq]

    async def list_tail(
        self, *, conversation_id: UUID, limit: int, before_seq: int | None = None
    ) -> list[ConversationMessage]:
        del conversation_id
        rows = (
            self.rows if before_seq is None else [m for m in self.rows if m.seq < before_seq]
        )
        return rows[-limit:] if limit > 0 else []


class _FakeUow:
    def __init__(
        self, conversation: Conversation, rows: list[ConversationMessage]
    ) -> None:
        self.conversations = _FakeConversations(conversation)
        self.conversation_messages = _FakeMessages(rows)

    async def __aenter__(self) -> _FakeUow:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


def _reader(turns: int):  # type: ignore[no-untyped-def]
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
        state=ConversationState.AI_ACTIVE,
        created_at=NOW,
        updated_at=NOW,
    )
    rows = [
        ConversationMessage(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=conversation.id,
            seq=i,
            role=MessageRole.USER if i % 2 else MessageRole.ASSISTANT,
            content=f"turn {i}",
            created_at=NOW,
        )
        for i in range(1, turns + 1)
    ]
    uow = _FakeUow(conversation, rows)

    def factory(user_id: UUID, tid: UUID) -> _FakeUow:
        del user_id, tid
        return uow

    use_case = ReadVisitorMessages(_FakeLookup(widget), factory)  # type: ignore[arg-type]

    async def read(**kwargs: object):  # type: ignore[no-untyped-def]
        return await use_case.execute(
            ReadVisitorMessagesQuery(
                widget_id=widget.id,
                session_id=session_id,
                session_origin=ORIGIN,
                **kwargs,  # type: ignore[arg-type]
            )
        )

    return read


class TestOpeningAThread:
    async def test_only_the_newest_page_is_restored(self) -> None:
        read = _reader(40)
        view = await read(history_limit=10)
        assert [t.seq for t in view.turns] == list(range(31, 41))

    async def test_there_is_more_above_it(self) -> None:
        read = _reader(40)
        view = await read(history_limit=10)
        assert view.has_more is True

    async def test_a_short_thread_reports_nothing_above(self) -> None:
        """Otherwise the widget would offer to load older turns that do not
        exist, and the visitor would scroll into a wait that never ends."""
        read = _reader(6)
        view = await read(history_limit=10)
        assert [t.seq for t in view.turns] == [1, 2, 3, 4, 5, 6]
        assert view.has_more is False


class TestScrollingUp:
    async def test_the_previous_page_is_contiguous_and_does_not_repeat(self) -> None:
        read = _reader(40)
        newest = await read(history_limit=10)
        older = await read(history_limit=10, before_seq=newest.turns[0].seq)
        assert [t.seq for t in older.turns] == list(range(21, 31))
        assert not {t.seq for t in older.turns} & {t.seq for t in newest.turns}

    async def test_reaching_the_beginning_stops_the_scroll(self) -> None:
        read = _reader(12)
        newest = await read(history_limit=10)
        older = await read(history_limit=10, before_seq=newest.turns[0].seq)
        assert [t.seq for t in older.turns] == [1, 2]
        assert older.has_more is False

    async def test_a_full_page_that_reaches_the_beginning_reports_no_more(
        self,
    ) -> None:
        """The case `has_more` cannot be inferred from page length, and the
        reason it is computed from the oldest `seq` instead.

        A thread of exactly ten turns returns a full page that is also the
        whole conversation. Length alone says "there is another page", the
        widget offers to load it, and the visitor scrolls up into nothing.
        Caught by mutating the check to `len(messages) == limit`, which every
        other test here accepted.
        """
        read = _reader(10)
        view = await read(history_limit=10)
        assert len(view.turns) == 10
        assert view.has_more is False

    async def test_a_full_page_that_lands_on_the_first_turn_reports_no_more(
        self,
    ) -> None:
        """Same boundary, reached by scrolling rather than by opening."""
        read = _reader(20)
        view = await read(history_limit=10, before_seq=11)
        assert [t.seq for t in view.turns] == list(range(1, 11))
        assert view.has_more is False

    async def test_a_page_is_ordered_oldest_first(self) -> None:
        """Selected newest-first because "the last N" cannot be expressed by an
        ascending LIMIT, but handed back in reading order -- the widget inserts
        them above the thread and would otherwise reverse the page."""
        read = _reader(40)
        view = await read(history_limit=10, before_seq=31)
        assert [t.seq for t in view.turns] == sorted(t.seq for t in view.turns)


class TestTheLivePollIsUnchanged:
    async def test_reading_forwards_still_returns_everything_after_the_cursor(
        self,
    ) -> None:
        """History mode is additive. The four-second poll that delivers a
        colleague's reply must behave exactly as it did -- it is the path a
        handed-off conversation depends on."""
        read = _reader(40)
        view = await read(after_seq=37)
        assert [t.seq for t in view.turns] == [38, 39, 40]
        assert view.has_more is False

    async def test_an_anonymous_caller_cannot_ask_for_the_whole_thread(self) -> None:
        """The cap is what stops one request pulling an unbounded transcript
        out of a public endpoint."""
        read = _reader(400)
        view = await read(history_limit=10_000)
        assert len(view.turns) == 50
