"""An ordinary widget question is now written to `conversations` /
`conversation_messages`, not only an escalated one.

Before this, a visitor's Q&A lived only in Redis for the length of the session
-- so the tenant's own console showed nothing for the overwhelming majority of
widget traffic, and retention had nothing to delete because nothing was
stored. These tests drive the real `AskWidget` use case with an in-memory
fake standing in for the unit of work, because the property under test is
*that persistence happens and in the right shape*, not what Postgres does
with the SQL.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from iam_platform.application.ai_resources.public_chat import AskWidget, AskWidgetCommand
from iam_platform.domain.ai_resources.entities import (
    ChatWidget,
    Conversation,
    ConversationMessage,
    MessageRole,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 1, 1, tzinfo=UTC)
ORIGIN = "https://help.acme.test"


class _FixedClock:
    def now(self) -> datetime:
        return NOW


def _widget(**overrides: object) -> ChatWidget:
    base: dict[str, object] = {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "knowledge_base_id": uuid4(),
        "name": "Help",
        "public_key": "wk_public",
        "allowed_origins": [ORIGIN],
        "created_by_membership_id": uuid4(),
        "created_at": NOW,
        "updated_at": NOW,
    }
    base.update(overrides)
    return ChatWidget(**base)  # type: ignore[arg-type]


@dataclass
class _FakeLookup:
    widgets: list[ChatWidget]

    async def find_by_public_key(self, public_key: str) -> ChatWidget | None:
        return next((w for w in self.widgets if w.public_key == public_key), None)

    async def find_by_widget_id(self, widget_id: UUID) -> ChatWidget | None:
        return next((w for w in self.widgets if w.id == widget_id), None)


@dataclass
class _FakeQuota:
    async def consume(self, *, widget_id: UUID, limit: int) -> bool:
        del widget_id, limit
        return True


@dataclass
class _FakePipeline:
    answer_text: str = "We are open 9 to 5."

    async def answer_from_namespace(self, question: str, *, namespace: str, **kwargs: object):  # type: ignore[no-untyped-def]
        del question, namespace, kwargs

        async def _tokens():  # type: ignore[no-untyped-def]
            yield self.answer_text

        from iam_platform.application.ai_resources.answer_question import AnswerStream

        return AnswerStream(tokens=_tokens(), citations=[], cited_labels=set())


class _FakeConversations:
    def __init__(self) -> None:
        self.rows: dict[UUID, Conversation] = {}
        self.saved: list[Conversation] = []

    async def find_by_visitor_session(
        self, *, tenant_id: UUID, visitor_session_id: UUID
    ) -> Conversation | None:
        return next(
            (
                c
                for c in self.rows.values()
                if c.tenant_id == tenant_id and c.visitor_session_id == visitor_session_id
            ),
            None,
        )

    async def add(self, conversation: Conversation) -> None:
        self.rows[conversation.id] = conversation

    async def save(self, conversation: Conversation) -> None:
        self.rows[conversation.id] = conversation
        self.saved.append(conversation)


class _FakeMessages:
    def __init__(self) -> None:
        self.rows: list[ConversationMessage] = []

    async def next_seq(self, conversation_id: UUID) -> int:
        existing = [m for m in self.rows if m.conversation_id == conversation_id]
        return len(existing) + 1

    async def add_many(self, messages: list[ConversationMessage]) -> None:
        self.rows.extend(messages)


class _FakeUow:
    def __init__(self, conversations: _FakeConversations, messages: _FakeMessages) -> None:
        self.conversations = conversations
        self.conversation_messages = messages

    async def __aenter__(self) -> _FakeUow:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


def _uow_factory(conversations: _FakeConversations, messages: _FakeMessages):  # type: ignore[no-untyped-def]
    def factory(user_id: UUID, tenant_id: UUID) -> _FakeUow:
        del user_id, tenant_id
        return _FakeUow(conversations, messages)

    return factory


async def _drain(stream) -> str:  # type: ignore[no-untyped-def]
    return "".join([piece async for piece in stream.tokens])


class TestOrdinaryQuestionsArePersisted:
    async def test_a_plain_question_creates_a_conversation_and_two_messages(
        self,
    ) -> None:
        widget = _widget()
        conversations, messages = _FakeConversations(), _FakeMessages()
        use_case = AskWidget(
            _FakeLookup([widget]),  # type: ignore[arg-type]
            _FakeQuota(),  # type: ignore[arg-type]
            _FakePipeline(answer_text="We are open 9 to 5."),  # type: ignore[arg-type]
            memory=None,
            uow_factory=_uow_factory(conversations, messages),  # type: ignore[arg-type]
            clock=_FixedClock(),
        )
        session_id = uuid4()

        stream = await use_case.execute(
            AskWidgetCommand(
                widget_id=widget.id,
                knowledge_base_id=widget.knowledge_base_id,
                question="What are your opening hours?",
                session_origin=ORIGIN,
                session_id=session_id,
            )
        )
        answer = await _drain(stream)

        assert answer == "We are open 9 to 5."
        assert len(conversations.rows) == 1
        conversation = next(iter(conversations.rows.values()))
        assert conversation.tenant_id == widget.tenant_id
        assert conversation.membership_id is None
        assert conversation.visitor_session_id == session_id

        assert len(messages.rows) == 2
        user_msg, assistant_msg = messages.rows
        assert user_msg.role is MessageRole.USER
        assert user_msg.content == "What are your opening hours?"
        assert assistant_msg.role is MessageRole.ASSISTANT
        assert assistant_msg.content == "We are open 9 to 5."
        # Written to the *conversation's* tenant, not left blank -- a message
        # RLS cannot scope is a message nobody but a platform session can read.
        assert user_msg.tenant_id == widget.tenant_id

    async def test_the_conversation_is_titled_from_the_first_question(self) -> None:
        widget = _widget()
        conversations, messages = _FakeConversations(), _FakeMessages()
        use_case = AskWidget(
            _FakeLookup([widget]),  # type: ignore[arg-type]
            _FakeQuota(),  # type: ignore[arg-type]
            _FakePipeline(),  # type: ignore[arg-type]
            memory=None,
            uow_factory=_uow_factory(conversations, messages),  # type: ignore[arg-type]
            clock=_FixedClock(),
        )

        stream = await use_case.execute(
            AskWidgetCommand(
                widget_id=widget.id,
                knowledge_base_id=widget.knowledge_base_id,
                question="  Do you offer   funded hours?  ",
                session_origin=ORIGIN,
                session_id=uuid4(),
            )
        )
        await _drain(stream)

        conversation = next(iter(conversations.rows.values()))
        assert conversation.title == "Do you offer funded hours?"

    async def test_a_second_question_in_the_same_session_appends_not_duplicates(
        self,
    ) -> None:
        widget = _widget()
        conversations, messages = _FakeConversations(), _FakeMessages()
        use_case = AskWidget(
            _FakeLookup([widget]),  # type: ignore[arg-type]
            _FakeQuota(),  # type: ignore[arg-type]
            _FakePipeline(),  # type: ignore[arg-type]
            memory=None,
            uow_factory=_uow_factory(conversations, messages),  # type: ignore[arg-type]
            clock=_FixedClock(),
        )
        session_id = uuid4()
        command = lambda q: AskWidgetCommand(  # noqa: E731
            widget_id=widget.id,
            knowledge_base_id=widget.knowledge_base_id,
            question=q,
            session_origin=ORIGIN,
            session_id=session_id,
        )

        await _drain(await use_case.execute(command("First question?")))
        await _drain(await use_case.execute(command("Second question?")))

        assert len(conversations.rows) == 1, "one session must be one conversation"
        assert len(messages.rows) == 4
        assert [m.seq for m in messages.rows] == [1, 2, 3, 4]

    async def test_a_persistence_failure_does_not_break_the_answer(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The visitor already has their answer by the time this runs. Losing
        the history row is the smaller harm than turning a successful answer
        into a visible error on the one surface with nobody to explain it to."""
        widget = _widget()

        class _BrokenConversations(_FakeConversations):
            async def add(self, conversation: Conversation) -> None:
                raise RuntimeError("connection reset")

        use_case = AskWidget(
            _FakeLookup([widget]),  # type: ignore[arg-type]
            _FakeQuota(),  # type: ignore[arg-type]
            _FakePipeline(answer_text="It still works."),  # type: ignore[arg-type]
            memory=None,
            uow_factory=_uow_factory(_BrokenConversations(), _FakeMessages()),  # type: ignore[arg-type]
            clock=_FixedClock(),
        )

        with caplog.at_level(logging.ERROR):
            stream = await use_case.execute(
                AskWidgetCommand(
                    widget_id=widget.id,
                    knowledge_base_id=widget.knowledge_base_id,
                    question="Anything?",
                    session_origin=ORIGIN,
                    session_id=uuid4(),
                )
            )
            answer = await _drain(stream)

        assert answer == "It still works."
        assert any("could not persist" in r.message for r in caplog.records)

    async def test_with_neither_sink_wired_the_stream_is_untouched(self) -> None:
        """No memory store and no uow factory: the exact shape a deployment
        without Redis or without this wiring has. Must behave exactly as
        before persistence existed, not as "persistence with nothing to
        write to" -- those look identical today and would diverge the moment
        the wrapper grows a default."""
        widget = _widget()
        use_case = AskWidget(
            _FakeLookup([widget]),  # type: ignore[arg-type]
            _FakeQuota(),  # type: ignore[arg-type]
            _FakePipeline(answer_text="Still fine."),  # type: ignore[arg-type]
        )

        stream = await use_case.execute(
            AskWidgetCommand(
                widget_id=widget.id,
                knowledge_base_id=widget.knowledge_base_id,
                question="Anything?",
                session_origin=ORIGIN,
                session_id=uuid4(),
            )
        )

        assert await _drain(stream) == "Still fine."


class _FailingPipeline:
    """Answers by raising -- the tenant is out of tokens, or the provider is
    down. Whatever the reason, the visitor got nothing."""

    async def answer_from_namespace(self, question: str, **kwargs: object):  # type: ignore[no-untyped-def]
        del question, kwargs
        raise RuntimeError("the model is unreachable")


class _CountingTenantQuota:
    """Records reservations and releases so a test can assert they balance."""

    def __init__(self) -> None:
        self.consumed = 0
        self.released = 0

    async def consume_message(
        self, *, tenant_id: UUID, limit: int | None, **kwargs: object
    ) -> bool:
        del tenant_id, limit
        self.consumed += 1
        return True

    async def release_message(self, *, tenant_id: UUID, **kwargs: object) -> None:
        del tenant_id
        self.released += 1


class TestAFailedAnswerDoesNotConsumeTheDailyAllowance:
    """The reservation is taken *before* the answer is attempted, so anything
    that then fails would permanently spend a message the visitor never
    received. `release_message` existed for exactly this and was called by
    nothing -- and the tenant-wide token check made the gap routine: a tenant
    at their token limit burned a message on every rejected attempt.
    """

    async def test_a_failed_answer_releases_the_reservation(self) -> None:
        widget = _widget()
        quota = _CountingTenantQuota()
        use_case = AskWidget(
            _FakeLookup([widget]),  # type: ignore[arg-type]
            _FakeQuota(),  # type: ignore[arg-type]
            _FailingPipeline(),  # type: ignore[arg-type]
            memory=None,
            tenant_quota=quota,
        )

        with pytest.raises(RuntimeError):
            await use_case.execute(
                AskWidgetCommand(
                    widget_id=widget.id,
                    knowledge_base_id=widget.knowledge_base_id,
                    question="What are your opening hours?",
                    session_origin=ORIGIN,
                    session_id=uuid4(),
                )
            )

        assert quota.consumed == 1
        assert quota.released == 1, "the message was consumed but never returned"

    async def test_a_successful_answer_keeps_the_reservation(self) -> None:
        """Guards against the release being unconditional, which would make the
        daily counter permanently zero."""
        widget = _widget()
        quota = _CountingTenantQuota()
        use_case = AskWidget(
            _FakeLookup([widget]),  # type: ignore[arg-type]
            _FakeQuota(),  # type: ignore[arg-type]
            _FakePipeline(answer_text="We are open 9 to 5."),  # type: ignore[arg-type]
            memory=None,
            tenant_quota=quota,
        )

        await use_case.execute(
            AskWidgetCommand(
                widget_id=widget.id,
                knowledge_base_id=widget.knowledge_base_id,
                question="What are your opening hours?",
                session_origin=ORIGIN,
                session_id=uuid4(),
            )
        )

        assert quota.consumed == 1
        assert quota.released == 0
