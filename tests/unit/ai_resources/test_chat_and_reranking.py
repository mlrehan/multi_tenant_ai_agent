"""The chat and reranking adapters.

Two of these exist because of defects found by running the pipeline against
real providers, not by reading their documentation:

- `temperature=0` was hardcoded, and the configured OpenAI model **rejects**
  any explicit temperature: every answer failed with a 400. The parameter is
  now omitted unless configured.
- Cohere returns reranked results carrying an `index` back into the request
  list. Assuming response order silently pairs each passage with another
  passage's relevance -- the same class of bug the embedding adapter guards
  against by sorting on `index`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import pytest

from iam_platform.application.ai_resources.ports import GroundingContext, RetrievedChunk
from iam_platform.core.config import CohereSettings, OpenAISettings
from iam_platform.infrastructure.chat.openai_chat import OpenAIChatModel, _render_prompt
from iam_platform.infrastructure.reranking.cohere_reranker import (
    CohereReranker,
    PassthroughReranker,
)

pytestmark = pytest.mark.unit


class _Delta:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _Choice:
    def __init__(self, content: str | None) -> None:
        self.delta = _Delta(content)


class _Event:
    def __init__(self, content: str | None) -> None:
        self.choices = [_Choice(content)]


@dataclass
class _FakeCompletions:
    requests: list[dict[str, Any]] = field(default_factory=list)
    pieces: list[str | None] = field(default_factory=lambda: ["Hello", " world"])

    async def create(self, **kwargs: Any) -> AsyncIterator[_Event]:
        self.requests.append(kwargs)
        return self._stream()

    async def _stream(self) -> AsyncIterator[_Event]:
        for piece in self.pieces:
            yield _Event(piece)


class _FakeOpenAI:
    def __init__(self) -> None:
        self.completions = _FakeCompletions()
        self.chat = self

    def last_request(self) -> dict[str, Any]:
        return self.completions.requests[-1]


def _context() -> list[GroundingContext]:
    chunk = RetrievedChunk(
        chunk_id=uuid4(), document_id=uuid4(), text="Refunds within 30 days.", score=0.9
    )
    return [GroundingContext(label="1", text=chunk.text, chunk=chunk)]


def _settings(**overrides: Any) -> OpenAISettings:
    return OpenAISettings(**overrides)


class TestTemperatureIsOptional:
    async def test_temperature_is_omitted_when_unset(self) -> None:
        """The default. Newer OpenAI models reject an explicit temperature --
        including an explicit null -- so the key must be absent, not None."""
        client = _FakeOpenAI()
        model = OpenAIChatModel(_settings(), client=client)

        [t async for t in model.stream_answer(
            question="q", context=_context(), system_prompt="s"
        )]

        assert "temperature" not in client.last_request()

    async def test_temperature_is_sent_when_configured(self) -> None:
        """An operator on a model that honours it can still pin determinism,
        which is the right value for grounded answering."""
        client = _FakeOpenAI()
        model = OpenAIChatModel(_settings(chat_temperature=0), client=client)

        [t async for t in model.stream_answer(
            question="q", context=_context(), system_prompt="s"
        )]

        assert client.last_request()["temperature"] == 0

class TestReasoningEffortIsOptional:
    """The largest latency control on the answer path, and the same opt-in
    shape as temperature for the same reason.

    Measured against the configured model with the real prompt, six questions
    each: unset gave a 2.11s median and a 10.80s worst case; `low` gave 1.24s
    and 1.58s. The win is the tail, not the median. A reasoning model emits
    nothing at all while it thinks, so an eleven-second outlier is a visitor
    watching an empty bubble -- not slow typing.
    """

    async def test_reasoning_effort_is_omitted_when_unset(self) -> None:
        """A non-reasoning model rejects the parameter outright, so the key
        must be absent rather than None -- exactly the trap that made
        `temperature=0` fail every answer."""
        client = _FakeOpenAI()
        model = OpenAIChatModel(_settings(), client=client)

        [t async for t in model.stream_answer(
            question="q", context=_context(), system_prompt="s"
        )]

        assert "reasoning_effort" not in client.last_request()

    async def test_reasoning_effort_is_sent_when_configured(self) -> None:
        client = _FakeOpenAI()
        model = OpenAIChatModel(_settings(chat_reasoning_effort="low"), client=client)

        [t async for t in model.stream_answer(
            question="q", context=_context(), system_prompt="s"
        )]

        assert client.last_request()["reasoning_effort"] == "low"

    async def test_the_value_is_passed_through_unvalidated(self) -> None:
        """Deliberate: valid values are model-dependent (the configured model
        accepts "low" and rejects "minimal"), and a local allowlist would go
        stale and start refusing values the API accepts. An unsupported value
        fails loudly at answer time instead."""
        client = _FakeOpenAI()
        model = OpenAIChatModel(_settings(chat_reasoning_effort="anything"), client=client)

        [t async for t in model.stream_answer(
            question="q", context=_context(), system_prompt="s"
        )]

        assert client.last_request()["reasoning_effort"] == "anything"

    async def test_empty_deltas_are_not_yielded(self) -> None:
        """OpenAI streams role-only and finish-reason events with no content;
        forwarding them would emit empty SSE frames to the client."""
        client = _FakeOpenAI()
        client.completions.pieces = [None, "real", None, " text"]
        model = OpenAIChatModel(_settings(), client=client)

        tokens = [t async for t in model.stream_answer(
            question="q", context=_context(), system_prompt="s"
        )]

        assert tokens == ["real", " text"]


class TestPerCallModelOverride:
    """`model_name`/`model_parameters` are what makes an assistant's own
    `model_configuration` actually change which model answers -- see
    `application/ai_resources/answer_question.py`. Every other test in this
    class calls `stream_answer` without them and must be unaffected; these
    prove the override path itself."""

    async def test_model_name_overrides_the_configured_default(self) -> None:
        client = _FakeOpenAI()
        model = OpenAIChatModel(_settings(chat_model="gpt-5.5"), client=client)

        [t async for t in model.stream_answer(
            question="q", context=_context(), system_prompt="s", model_name="gpt-5.5-mini",
        )]

        assert client.last_request()["model"] == "gpt-5.5-mini"

    async def test_omitted_model_name_falls_back_to_the_configured_default(self) -> None:
        client = _FakeOpenAI()
        model = OpenAIChatModel(_settings(chat_model="gpt-5.5"), client=client)

        [t async for t in model.stream_answer(question="q", context=_context(), system_prompt="s")]

        assert client.last_request()["model"] == "gpt-5.5"

    async def test_model_parameters_temperature_overrides_the_platform_setting(self) -> None:
        client = _FakeOpenAI()
        model = OpenAIChatModel(_settings(chat_temperature=0), client=client)

        [t async for t in model.stream_answer(
            question="q", context=_context(), system_prompt="s",
            model_parameters={"temperature": 0.7},
        )]

        assert client.last_request()["temperature"] == 0.7

    async def test_model_parameters_reasoning_effort_overrides_the_platform_setting(self) -> None:
        client = _FakeOpenAI()
        model = OpenAIChatModel(_settings(chat_reasoning_effort="low"), client=client)

        [t async for t in model.stream_answer(
            question="q", context=_context(), system_prompt="s",
            model_parameters={"reasoning_effort": "high"},
        )]

        assert client.last_request()["reasoning_effort"] == "high"

    async def test_a_malformed_temperature_degrades_to_the_platform_default_rather_than_failing(
        self,
    ) -> None:
        """`parameters` comes from a tenant-editable row. A bad value there
        must not take down every answer through that assistant."""
        client = _FakeOpenAI()
        model = OpenAIChatModel(_settings(chat_temperature=0), client=client)

        [t async for t in model.stream_answer(
            question="q", context=_context(), system_prompt="s",
            model_parameters={"temperature": "not-a-number"},
        )]

        assert client.last_request()["temperature"] == 0

    async def test_an_unrecognised_parameter_key_is_ignored_not_rejected(self) -> None:
        client = _FakeOpenAI()
        model = OpenAIChatModel(_settings(), client=client)

        [t async for t in model.stream_answer(
            question="q", context=_context(), system_prompt="s",
            model_parameters={"top_p": 0.5},
        )]

        assert "top_p" not in client.last_request()
        assert "temperature" not in client.last_request()


class TestPromptStructure:
    def test_sources_precede_the_question(self) -> None:
        """A model that reads the question first is likelier to go looking for
        support for an answer it has already formed."""
        rendered = _render_prompt("What is the refund window?", _context())

        assert rendered.index("Sources:") < rendered.index("Question:")

    def test_each_passage_is_fenced_and_labelled(self) -> None:
        """The fence is what stops retrieved text -- which on the crawled web
        routinely contains things that look like instructions or citations --
        being read as part of the prompt."""
        rendered = _render_prompt("q", _context())

        assert "<<<SOURCE 1>>>" in rendered
        assert "<<<END 1>>>" in rendered


@dataclass
class _RerankResult:
    index: int
    relevance_score: float


@dataclass
class _RerankResponse:
    results: list[_RerankResult]


class _FakeCohere:
    def __init__(self, results: list[_RerankResult]) -> None:
        self._results = results

    async def rerank(self, **_: Any) -> _RerankResponse:
        return _RerankResponse(results=self._results)


def _chunks() -> list[RetrievedChunk]:
    return [
        RetrievedChunk(chunk_id=uuid4(), document_id=uuid4(), text=t, score=s)
        for t, s in [("first", 0.9), ("second", 0.8), ("third", 0.7)]
    ]


class TestRerankerMapsThroughIndex:
    async def test_results_are_mapped_by_index_not_response_order(self) -> None:
        """The bug this guards against is silent: assuming response order pairs
        each passage with another passage's relevance, producing a confidently
        misordered context that still looks reasonable."""
        chunks = _chunks()
        # Cohere says the *third* passage is most relevant.
        client = _FakeCohere([_RerankResult(index=2, relevance_score=0.99),
                              _RerankResult(index=0, relevance_score=0.42)])
        reranker = CohereReranker(CohereSettings(), client=client)

        reranked = await reranker.rerank(query="q", chunks=chunks, top_n=2)

        assert [r.chunk.text for r in reranked] == ["third", "first"]
        assert reranked[0].relevance == pytest.approx(0.99)

    async def test_an_out_of_range_index_is_skipped_not_crashed(self) -> None:
        client = _FakeCohere([_RerankResult(index=99, relevance_score=0.9),
                              _RerankResult(index=1, relevance_score=0.5)])
        reranker = CohereReranker(CohereSettings(), client=client)

        reranked = await reranker.rerank(query="q", chunks=_chunks(), top_n=2)

        assert [r.chunk.text for r in reranked] == ["second"]

    async def test_no_chunks_means_no_call(self) -> None:
        """Sending an empty document list is a billable request that can only
        return nothing."""

        class _Exploding:
            async def rerank(self, **_: Any) -> Any:
                raise AssertionError("must not call the reranker with no chunks")

        reranker = CohereReranker(CohereSettings(), client=_Exploding())

        assert await reranker.rerank(query="q", chunks=[], top_n=5) == []


class TestPassthroughReranker:
    async def test_it_preserves_retrieval_order_and_scores(self) -> None:
        """Used when Cohere is unconfigured. Deliberately degrades quality
        rather than refusing: without a reranker there are still real, relevant
        passages -- just ordered by embedding similarity. Contrast the vector
        client, which raises, because without it there are no passages at all."""
        chunks = _chunks()

        reranked = await PassthroughReranker().rerank(query="q", chunks=chunks, top_n=2)

        assert [r.chunk.text for r in reranked] == ["first", "second"]
        # Vector scores carried through unrescaled, so a caller comparing
        # across configurations can see they are a different quantity.
        assert reranked[0].relevance == pytest.approx(0.9)
