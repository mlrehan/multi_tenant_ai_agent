"""An answer is shown only if it came from the sources -- or honestly says not.

Found live: "What is the capital city of Australia?" was answered "Canberra"
from general knowledge, with no citation, and reached the visitor. And "the
sources don't say -- contact us [2]" counted as `grounded` because it cited
the contact page, so it never reached the "couldn't answer" feed.

Driven through the real `answer_from_namespace`, so what is asserted is what
a caller -- the widget or the console -- is actually streamed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest

from iam_platform.application.ai_resources.answer_gate import (
    NO_ANSWER_MARKER,
    RESTRICTED_MARKER,
    WITHHELD_REPLY,
    HeldText,
    should_withhold,
)
from iam_platform.application.ai_resources.answer_question import AnswerQuestion, AnswerStream
from iam_platform.application.ai_resources.ports import TokenUsage
from tests.unit.ai_resources.test_answer_question import _chunk
from tests.unit.ai_resources.test_usage_ledger import TENANT, _Events, _pipeline

pytestmark = pytest.mark.unit


class _Scripted:
    """A chat model that streams exactly these pieces."""

    def __init__(self, *pieces: str) -> None:
        self._pieces = pieces

    async def _stream(self, usage: TokenUsage | None) -> AsyncIterator[str]:
        for piece in self._pieces:
            yield piece

    def stream_answer(self, *, usage: TokenUsage | None = None, **kwargs: object) -> AsyncIterator[str]:
        del kwargs
        return self._stream(usage)


def _with(*pieces: str) -> AnswerQuestion:
    pipeline = _pipeline(_Events(), [_chunk("Academic discount up to 20%."), _chunk("Call us.")])
    pipeline._chat_model = _Scripted(*pieces)  # type: ignore[assignment]
    return pipeline


async def _ask(pipeline: AnswerQuestion, question: str) -> tuple[list[str], AnswerStream]:
    stream = await pipeline.answer_from_namespace(
        question, namespace=f"{TENANT}/{uuid4()}", tenant_id=TENANT
    )
    return [p async for p in stream.tokens], stream


class TestAnUncitedAnswerIsWithheld:
    async def test_general_knowledge_never_reaches_the_caller(self) -> None:
        pieces, stream = await _ask(
            _with("The capital city of ", "Australia is **Canberra**."),
            "What is the capital city of Australia?",
        )
        assert "".join(pieces) == WITHHELD_REPLY
        assert not any("Canberra" in p for p in pieces)
        assert stream.outcome == "withheld"

    async def test_a_cited_answer_streams_unchanged(self) -> None:
        pieces, stream = await _ask(
            _with("We offer an academic ", "discount of up to 20% [1]", " for students."),
            "What discounts do you offer?",
        )
        assert "".join(pieces) == "We offer an academic discount of up to 20% [1] for students."
        assert stream.outcome == "grounded"

    async def test_text_after_the_first_citation_streams_as_it_arrives(self) -> None:
        # Held only until the first real citation, then released piece by piece.
        pieces, _ = await _ask(
            _with("Up to 20% [1].", " Ask ", "for an invoice."),
            "What discounts do you offer?",
        )
        assert pieces == ["Up to 20% [1].", " Ask ", "for an invoice."]

    async def test_a_fabricated_citation_does_not_release_the_answer(self) -> None:
        pieces, stream = await _ask(
            _with("Canberra is the capital [9]."), "What is the capital city of Australia?"
        )
        assert "".join(pieces) == WITHHELD_REPLY
        assert stream.outcome == "withheld"


class TestTheNoAnswerMarker:
    async def test_it_is_never_shown_and_the_answer_is_not_in_sources(self) -> None:
        pieces, stream = await _ask(
            _with(NO_ANSWER_MARKER, " The sources don't say whether dogs are allowed.",
                  " You can contact the academy [2]."),
            "Can I bring my dog to class?",
        )
        shown = "".join(pieces)
        assert NO_ANSWER_MARKER not in shown
        assert shown.startswith("The sources don't say")
        assert stream.outcome == "not_in_sources"

    async def test_a_marker_split_across_chunks_is_still_removed(self) -> None:
        pieces, stream = await _ask(
            _with("[NO", "_ANS", "WER]", "I can't confirm that. Call us [2]."),
            "Can I bring my dog to class?",
        )
        assert "".join(pieces) == "I can't confirm that. Call us [2]."
        assert stream.outcome == "not_in_sources"

    async def test_a_declared_uncited_answer_is_shown_as_written(self) -> None:
        # Saying "the sources don't cover this" is honest; it is not replaced.
        pieces, stream = await _ask(
            _with(NO_ANSWER_MARKER, "The sources don't cover car parking."),
            "Is there a car park?",
        )
        assert "".join(pieces) == "The sources don't cover car parking."
        assert stream.outcome == "not_in_sources"


class TestExemptionsFailTowardShowing:
    @pytest.mark.parametrize(
        ("question", "answer"),
        [
            ("hello", "Hello! How can I help?"),
            # Answered from the tenant's configured role; no source to cite.
            ("Who are you and what can you help me with?", "I'm the nursery's front-desk assistant."),
            ("I'd like to speak to a person please", "I can connect you with the team."),
            (
                "I think a child at the nursery is being hurt",
                "Please speak to the Designated Safeguarding Lead straight away.",
            ),
            ("Something is wrong", "If anyone is in immediate danger, call 999 now."),
            # The question carries the risk signal; the reply names no service.
            ("My child is choking at pickup", "Stay with them and get help right away."),
            ("Tell me about the course", "Which course do you mean -- Python or SQL?"),
        ],
    )
    async def test_an_exempt_uncited_reply_is_shown(self, question: str, answer: str) -> None:
        pieces, stream = await _ask(_with(answer), question)
        assert "".join(pieces) == answer
        assert stream.outcome == "uncited"

    def test_an_ordinary_factual_question_is_not_exempt(self) -> None:
        assert should_withhold("What is the capital city of Australia?", "Canberra.")

    def test_a_factual_question_worded_like_an_about_you_one_is_not_exempt(self) -> None:
        assert should_withhold("What are you offering for schools?", "A 20% discount.")


class TestHeldText:
    def test_a_citation_is_not_held_back(self) -> None:
        held = HeldText()
        held.push("See [1")
        assert held.take() == "See "
        held.push("] now")
        assert held.take() == "[1] now"

    def test_final_releases_everything(self) -> None:
        held = HeldText()
        held.push("An open bracket [")
        assert held.take(final=True) == "An open bracket ["


class TestTheRestrictedMarker:
    """A decline because of the tenant's avoid rules is not a gap in the
    sources, so it is recorded as `restricted` and never as `not_in_sources`."""

    async def test_it_is_never_shown_and_the_answer_is_restricted(self) -> None:
        pieces, stream = await _ask(
            _with(RESTRICTED_MARKER, " Pricing questions are handled by the admissions team."),
            "What discounts do you offer?",
        )
        assert "".join(pieces) == "Pricing questions are handled by the admissions team."
        assert stream.outcome == "restricted"

    async def test_a_split_restricted_marker_is_still_removed(self) -> None:
        pieces, stream = await _ask(
            _with("[RESTR", "ICTED]", "I can't discuss prices here."),
            "What discounts do you offer?",
        )
        assert "".join(pieces) == "I can't discuss prices here."
        assert stream.outcome == "restricted"

    async def test_a_restriction_outranks_not_in_sources(self) -> None:
        _, stream = await _ask(
            _with(RESTRICTED_MARKER, NO_ANSWER_MARKER, "I can't discuss that."),
            "What discounts do you offer?",
        )
        assert stream.outcome == "restricted"
