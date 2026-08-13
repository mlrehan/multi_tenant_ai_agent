"""Groundedness: the property the RAG pipeline exists to protect.

A language model asked something it has no sources for will answer anyway,
fluently, and a tenant's customers cannot tell that apart from a real answer.
These tests drive the real `AnswerQuestion` and assert on the three defences
that make that harder — refusing without passages, validating citations against
what was actually sent, and never letting the caller choose the search
namespace.

The chat model is a fake because the property under test is *what the pipeline
sends and accepts*, not what OpenAI writes. A fake that records its inputs is
the only way to assert "the model was never called" — which is the whole point
of the first test below.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from iam_platform.application.ai_resources.ports import (
    GroundingContext,
    RerankedChunk,
    RetrievedChunk,
)

pytestmark = pytest.mark.unit

QUERY_PERMISSION = frozenset({"tenant.knowledge_bases.query"})


@dataclass
class _FakeChatModel:
    """Records everything it was asked to answer from, including the
    per-call model overrides an assistant may supply."""

    reply: str = "The refund window is 30 days [1]."
    calls: list[tuple[str, list[GroundingContext], str]] = field(default_factory=list)
    model_calls: list[tuple[str | None, dict[str, object] | None]] = field(
        default_factory=list
    )

    def stream_answer(
        self,
        *,
        question: str,
        context: list[GroundingContext],
        system_prompt: str,
        model_name: str | None = None,
        model_parameters: dict[str, object] | None = None,
        # Accepted and ignored: the port grew these for budget metering and
        # BYOK. A fake narrower than the Protocol it stands in for fails with a
        # TypeError on every caller, which is how six unrelated tests broke.
        usage: object | None = None,
        credential_ciphertext: bytes | None = None,
    ) -> AsyncIterator[str]:
        self.calls.append((question, context, system_prompt))
        self.model_calls.append((model_name, model_parameters))
        return self._stream()

    async def _stream(self) -> AsyncIterator[str]:
        # Token by token, so the citation tracker is exercised across chunk
        # boundaries rather than handed one complete string.
        for piece in self.reply.split(" "):
            yield piece + " "


@dataclass
class _FakeVectorSearch:
    chunks: list[RetrievedChunk] = field(default_factory=list)
    namespaces: list[str] = field(default_factory=list)

    async def search_chunks(
        self, *, namespace: str, query_text: str, top_k: int
    ) -> list[RetrievedChunk]:
        self.namespaces.append(namespace)
        return self.chunks[:top_k]

    async def ensure_namespace(self, *, namespace: str, dimensions: int) -> None: ...
    async def upsert(self, *, namespace: str, chunks: list[object]) -> None: ...
    async def delete_document(self, *, namespace: str, document_id: UUID) -> None: ...
    async def query(
        self, *, namespace: str, query_text: str, top_k: int
    ) -> list[tuple[UUID, float]]: ...


class _OrderPreservingReranker:
    async def rerank(
        self, *, query: str, chunks: list[RetrievedChunk], top_n: int
    ) -> list[RerankedChunk]:
        return [RerankedChunk(chunk=c, relevance=c.score) for c in chunks[:top_n]]


def _chunk(text: str, score: float = 0.9) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid4(),
        document_id=uuid4(),
        text=text,
        score=score,
        source_location="page 1",
    )


NOW = datetime(2026, 1, 1, tzinfo=UTC)
