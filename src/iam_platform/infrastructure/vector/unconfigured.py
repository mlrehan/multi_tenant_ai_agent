"""``VectorSearchClient`` for a deployment with no embedding provider configured.

Knowledge-base search is an optional capability of this platform: an operator
running it purely for identity and authorization has no reason to hold an
OpenAI key, and the API must boot and serve normally without one.

What it must *not* do is pretend. The obvious shortcut -- fall back to the
in-memory client -- would answer every search with an empty result set, which
is indistinguishable from "this knowledge base genuinely has no matching
content". A tenant would see a working-looking search that silently never
finds anything, and nothing in the logs would say why.

This project has been bitten by that exact shape three times (Phase 9's
unresolved ``secret://`` references, a ``/readyz`` that could never fail, and
resources that were never disposed -- see
docs/22-deployment-and-operations.md). So the unconfigured path raises, with
the name of the setting that would fix it.
"""

from __future__ import annotations

from uuid import UUID

from iam_platform.application.ai_resources.ports import RetrievedChunk, VectorChunk

_MESSAGE = (
    "knowledge-base search is not configured: set OPENAI__API_KEY (and QDRANT__URL "
    "if the vector store is not on localhost). Refusing to answer rather than "
    "returning an empty result set, which would look like a knowledge base with "
    "no matching content."
)


class UnconfiguredVectorSearchClient:
    async def ensure_namespace(self, *, namespace: str, dimensions: int) -> None:
        raise RuntimeError(_MESSAGE)

    async def upsert(self, *, namespace: str, chunks: list[VectorChunk]) -> None:
        raise RuntimeError(_MESSAGE)

    async def delete_document(self, *, namespace: str, document_id: UUID) -> None:
        raise RuntimeError(_MESSAGE)

    async def query(
        self, *, namespace: str, query_text: str, top_k: int
    ) -> list[tuple[UUID, float]]:
        raise RuntimeError(_MESSAGE)

    async def search_chunks(
        self, *, namespace: str, query_text: str, top_k: int
    ) -> list[RetrievedChunk]:
        # Raising, never returning []. An empty passage list would make the
        # generator answer "I don't have information about that" -- a fluent,
        # confident, wrong answer indistinguishable from a genuinely empty
        # knowledge base, and invisible in logs. The same reasoning as `query`.
        raise RuntimeError(_MESSAGE)
