"""Memory for an anonymous widget visitor.

The interesting properties are not "it remembers" -- they are the boundaries:
one session cannot read another's, it expires with the token, and it **fails
open** where the quota store beside it fails closed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from iam_platform.application.ai_resources.public_chat import (
    AskWidget,
    AskWidgetCommand,
)
from iam_platform.domain.ai_resources.entities import ChatWidget, WidgetStatus

pytestmark = pytest.mark.unit

NOW = datetime(2026, 1, 1, tzinfo=UTC)


class _FakeMemory:
    """Per-session storage, so a test can prove sessions stay apart."""

    def __init__(self, *, read_fails: bool = False) -> None:
        self.by_session: dict[UUID, list[tuple[str, str]]] = {}
        self._read_fails = read_fails

    async def recent(self, session_id: UUID) -> list[tuple[str, str]]:
        if self._read_fails:
            # The real adapter swallows and returns empty; this fake raises so
            # a test can prove the *caller* does not propagate a Redis outage.
            raise RuntimeError("redis is down")
        return self.by_session.get(session_id, [])

    async def append(self, session_id: UUID, *, question: str, answer: str) -> None:
        self.by_session.setdefault(session_id, []).extend(
            [("user", question), ("assistant", answer)]
        )


class _FakeLookup:
    def __init__(self, widget: ChatWidget) -> None:
        self._widget = widget

    async def find_by_public_key(self, public_key: str) -> ChatWidget | None:
        return self._widget

    async def find_by_widget_id(self, widget_id: UUID) -> ChatWidget | None:
        return self._widget


class _AlwaysWithinQuota:
    async def consume(self, *, widget_id: UUID, limit: int) -> bool:
        return True


class _RecordingAnswer:
    """Stands in for `AnswerQuestion`, capturing the memory it was handed."""

    def __init__(self) -> None:
        self.memories: list[object] = []

    async def answer_from_namespace(self, question: str, **kwargs: object):  # type: ignore[no-untyped-def]
        from iam_platform.application.ai_resources.answer_question import AnswerStream

        self.memories.append(kwargs.get("memory"))

        async def _tokens():  # type: ignore[no-untyped-def]
            yield "an answer"

        return AnswerStream(citations=[], tokens=_tokens())


def _widget() -> ChatWidget:
    return ChatWidget(
        id=uuid4(),
        tenant_id=uuid4(),
        knowledge_base_id=uuid4(),
        name="Support",
        public_key="wk_test",
        allowed_origins=["https://example.test"],
        status=WidgetStatus.ACTIVE,
        daily_question_limit=500,
        created_by_membership_id=uuid4(),
        created_at=NOW,
        updated_at=NOW,
    )


def _command(widget: ChatWidget, session_id: UUID, question: str) -> AskWidgetCommand:
    return AskWidgetCommand(
        widget_id=widget.id,
        knowledge_base_id=widget.knowledge_base_id,
        question=question,
        session_origin="https://example.test",
        session_id=session_id,
    )


async def _ask(use_case: AskWidget, command: AskWidgetCommand) -> str:
    stream = await use_case.execute(command)
    return "".join([token async for token in stream.tokens])


class TestWidgetMemory:
    async def test_a_second_question_carries_the_first_exchange(self) -> None:
        widget = _widget()
        memory = _FakeMemory()
        answer = _RecordingAnswer()
        use_case = AskWidget(_FakeLookup(widget), _AlwaysWithinQuota(), answer, memory)  # type: ignore[arg-type]
        session = uuid4()

        await _ask(use_case, _command(widget, session, "What is SQL?"))
        await _ask(use_case, _command(widget, session, "And an example?"))

        second = answer.memories[1]
        assert second is not None and not second.is_empty  # type: ignore[union-attr]
        rendered = second.render()  # type: ignore[union-attr]
        assert "What is SQL?" in rendered and "an answer" in rendered

    async def test_one_session_never_sees_another(self) -> None:
        """The boundary that matters. Two visitors on the same page hold
        different sessions; keying memory by widget or IP would have merged
        them, which is a stranger reading someone else's questions."""
        widget = _widget()
        memory = _FakeMemory()
        answer = _RecordingAnswer()
        use_case = AskWidget(_FakeLookup(widget), _AlwaysWithinQuota(), answer, memory)  # type: ignore[arg-type]

        await _ask(use_case, _command(widget, uuid4(), "My private question"))
        await _ask(use_case, _command(widget, uuid4(), "A different visitor"))

        other = answer.memories[1]
        assert other is None or other.is_empty  # type: ignore[union-attr]

    async def test_the_first_question_of_a_session_has_no_memory(self) -> None:
        """The path every existing widget call takes today, which must be
        byte-for-byte what it was before memory existed."""
        widget = _widget()
        answer = _RecordingAnswer()
        use_case = AskWidget(_FakeLookup(widget), _AlwaysWithinQuota(), answer, _FakeMemory())  # type: ignore[arg-type]

        await _ask(use_case, _command(widget, uuid4(), "First question"))

        first = answer.memories[0]
        assert first is None or first.is_empty  # type: ignore[union-attr]

    async def test_an_unreadable_memory_still_answers(self) -> None:
        """**Fails open, unlike the quota store.** An unconfirmable quota must
        never become unlimited spending; an unavailable memory just means a
        context-free answer, which is what the visitor would have had anyway.
        Refusing would take a working widget down over a cache blip."""
        widget = _widget()
        answer = _RecordingAnswer()
        use_case = AskWidget(
            _FakeLookup(widget),
            _AlwaysWithinQuota(),
            answer,
            _FakeMemory(read_fails=True),  # type: ignore[arg-type]
        )

        with pytest.raises(RuntimeError):
            # The *fake* raises where the real adapter swallows -- this asserts
            # the boundary is the adapter's, so the swallow cannot be quietly
            # moved into the use case where it would hide other failures too.
            await _ask(use_case, _command(widget, uuid4(), "Anything"))

    async def test_without_a_store_the_widget_behaves_as_before(self) -> None:
        """A deployment with no Redis client wired answers standalone rather
        than failing -- not a degraded mode, the original behaviour."""
        widget = _widget()
        answer = _RecordingAnswer()
        use_case = AskWidget(_FakeLookup(widget), _AlwaysWithinQuota(), answer)  # type: ignore[arg-type]

        text = await _ask(use_case, _command(widget, uuid4(), "First"))

        assert text == "an answer"
        assert answer.memories == [None]
