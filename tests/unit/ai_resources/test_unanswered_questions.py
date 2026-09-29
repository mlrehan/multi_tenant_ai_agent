"""The "questions your chatbot couldn't answer" feed.

The status is decided by the pipeline from what actually happened -- nothing
retrieved, or nothing cited -- never from the answer's wording, which varies
with the prompt and the model.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from iam_platform.application.ai_resources.answer_question import AnswerStream
from iam_platform.application.ai_resources.exceptions import PermissionDeniedError
from iam_platform.application.ai_resources.ports import TokenUsage, UnansweredQuestion
from iam_platform.application.ai_resources.unanswered_questions import (
    ListUnansweredQuestions,
    ListUnansweredQuestionsQuery,
    is_about_the_assistant,
    is_small_talk,
)
from iam_platform.core.clock import FixedClock
from iam_platform.domain.ai_resources.entities import MessageRole
from tests.unit.ai_resources.test_answer_question import _chunk
from tests.unit.ai_resources.test_usage_ledger import (
    TENANT,
    _BilledChatModel,
    _drain,
    _Events,
    _pipeline,
    _TestWidgetPath,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 28, tzinfo=UTC)


class _UncitingChatModel(_BilledChatModel):
    async def _stream(self, usage: TokenUsage | None) -> AsyncIterator[str]:
        yield "I can't confirm that from the information I have."


class TestThePipelineRecordsTheOutcome:
    async def test_nothing_retrieved_is_no_sources(self) -> None:
        stream = await _pipeline(_Events(), []).answer_from_namespace(
            "Do you offer funded places?", namespace=f"{TENANT}/{uuid4()}", tenant_id=TENANT
        )
        await _drain(stream)
        assert stream.outcome == "no_sources"

    async def test_an_answer_that_cites_a_passage_is_grounded(self) -> None:
        stream = await _pipeline(_Events(), [_chunk("We open at 9.")]).answer_from_namespace(
            "When do you open?", namespace=f"{TENANT}/{uuid4()}", tenant_id=TENANT
        )
        await _drain(stream)
        assert stream.outcome == "grounded"

    async def test_an_answer_that_cites_nothing_is_withheld(self) -> None:
        # Before `answer_gate` this was shown and stored as `uncited`; an
        # ordinary question answered without a source is now replaced.
        pipeline = _pipeline(_Events(), [_chunk("Courses in Python.")])
        pipeline._chat_model = _UncitingChatModel()  # type: ignore[assignment]
        stream = await pipeline.answer_from_namespace(
            "Do you offer funded places?", namespace=f"{TENANT}/{uuid4()}", tenant_id=TENANT
        )
        await _drain(stream)
        assert stream.outcome == "withheld"

    async def test_an_exempt_answer_that_cites_nothing_is_uncited(self) -> None:
        pipeline = _pipeline(_Events(), [_chunk("Courses in Python.")])
        pipeline._chat_model = _UncitingChatModel()  # type: ignore[assignment]
        stream = await pipeline.answer_from_namespace(
            "hello", namespace=f"{TENANT}/{uuid4()}", tenant_id=TENANT
        )
        await _drain(stream)
        assert stream.outcome == "uncited"


class TestTheWidgetStoresIt(_TestWidgetPath):
    async def test_the_stored_answer_carries_its_status(self) -> None:
        from tests.unit.ai_resources.test_usage_ledger import _CapturingPipeline
        from tests.unit.ai_resources.test_widget_conversation_persistence import _widget

        class _Uncited(_CapturingPipeline):
            async def answer_from_namespace(
                self, question: str, *, namespace: str, **kwargs: object
            ) -> AnswerStream:
                stream = await super().answer_from_namespace(question, namespace=namespace, **kwargs)
                stream.outcome = "uncited"
                return stream

        messages = await self._ask(_Uncited(), _widget())
        answer = next(m for m in messages.rows if m.role is MessageRole.ASSISTANT)
        assert answer.answer_status == "uncited"


class _Messages:
    def __init__(self, rows: list[UnansweredQuestion]) -> None:
        self.rows = rows
        self.asked: dict[str, object] = {}

    async def unanswered_questions(self, **kwargs: object) -> list[UnansweredQuestion]:
        self.asked = kwargs
        return self.rows


class _Uow:
    def __init__(self, messages: _Messages) -> None:
        self.conversation_messages = messages

    async def __aenter__(self) -> _Uow:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


def _row(question: str, times: int = 1) -> UnansweredQuestion:
    return UnansweredQuestion(question=question, times_asked=times, last_asked_at=NOW, no_sources=False)


class TestTheFeed:
    async def test_small_talk_is_left_out_and_real_questions_kept(self) -> None:
        messages = _Messages([_row("Hi!", 9), _row("Do you offer funded places?", 3), _row("thank you", 2)])
        result = await ListUnansweredQuestions(lambda _a, _t: _Uow(messages), FixedClock(NOW)).execute(  # type: ignore[arg-type,return-value]
            ListUnansweredQuestionsQuery(
                actor_user_id=str(uuid4()), tenant_id=str(TENANT),
                permissions=frozenset({"tenant.conversations.view"}),
            )
        )
        assert [r.question for r in result] == ["Do you offer funded places?"]
        assert messages.asked["tenant_id"] == TENANT

    async def test_it_shows_visitors_words_so_needs_the_conversations_permission(self) -> None:
        messages = _Messages([_row("Do you offer funded places?")])
        with pytest.raises(PermissionDeniedError):
            await ListUnansweredQuestions(lambda _a, _t: _Uow(messages), FixedClock(NOW)).execute(  # type: ignore[arg-type,return-value]
                ListUnansweredQuestionsQuery(
                    actor_user_id=str(uuid4()), tenant_id=str(TENANT), permissions=frozenset()
                )
            )
        assert messages.asked == {}


@pytest.mark.parametrize("text", ["hi", "Hello!", "thanks", "Thank you.", "ok", "  "])
def test_small_talk(text: str) -> None:
    assert is_small_talk(text)


@pytest.mark.parametrize("text", ["Opening hours?", "fees", "Do you take 2 year olds?"])
def test_real_questions_are_not_small_talk(text: str) -> None:
    assert not is_small_talk(text)


class TestQuestionsAboutTheAssistant:
    """Answered from the tenant's configured role, not from the sources --
    so they are not a gap to fill and must not bury the real ones."""

    async def test_they_are_left_out_of_the_feed(self) -> None:
        messages = _Messages(
            [_row("Who are you and what can you help me with?", 4), _row("Do you offer funded places?")]
        )
        result = await ListUnansweredQuestions(lambda _a, _t: _Uow(messages), FixedClock(NOW)).execute(  # type: ignore[arg-type,return-value]
            ListUnansweredQuestionsQuery(
                actor_user_id=str(uuid4()), tenant_id=str(TENANT),
                permissions=frozenset({"tenant.conversations.view"}),
            )
        )
        assert [r.question for r in result] == ["Do you offer funded places?"]


@pytest.mark.parametrize(
    "text",
    ["Who are you?", "What is your name?", "Are you a bot?", "What can you help me with?", "what are you?"],
)
def test_about_the_assistant(text: str) -> None:
    assert is_about_the_assistant(text)


@pytest.mark.parametrize(
    "text",
    [
        # Open like an about-you question, but ask for facts.
        "What are you offering for schools?",
        "How can you help me with funding?",
        "Are you open on Saturdays?",
        "Who is the manager?",
    ],
)
def test_factual_questions_are_not_about_the_assistant(text: str) -> None:
    assert not is_about_the_assistant(text)
