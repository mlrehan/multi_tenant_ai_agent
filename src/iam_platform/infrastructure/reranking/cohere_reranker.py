"""`Reranker` backed by Cohere Rerank.

Lazy import inside the constructor, matching `boto3` and `docling` elsewhere:
the API process constructs the container at startup and most requests never
rerank anything.

**Order is the contract.** Cohere returns results by relevance with an `index`
pointing back into the list that was sent. This adapter maps through that index
rather than assuming the response arrives in request order — the same class of
bug the OpenAI embedding adapter guards against by sorting on `index`, where
getting it wrong silently pairs each passage with another passage's score.
"""

from __future__ import annotations

import logging
from typing import Any

from iam_platform.application.ai_resources.ports import RerankedChunk, RetrievedChunk
from iam_platform.core.config import CohereSettings

logger = logging.getLogger("iam_platform.infrastructure.reranking")


class CohereReranker:
    def __init__(self, settings: CohereSettings, *, client: Any | None = None) -> None:
        self._model = settings.rerank_model
        if client is not None:
            self._client: Any = client
            return

        import cohere

        self._client = cohere.AsyncClientV2(api_key=settings.api_key.get_secret_value())

    async def rerank(
        self, *, query: str, chunks: list[RetrievedChunk], top_n: int
    ) -> list[RerankedChunk]:
        if not chunks:
            return []

        response = await self._client.rerank(
            model=self._model,
            query=query,
            documents=[c.text for c in chunks],
            top_n=min(top_n, len(chunks)),
        )

        reranked: list[RerankedChunk] = []
        for result in response.results:
            index = int(result.index)
            # Defensive: an out-of-range index would otherwise raise deep in the
            # pipeline with no indication that the reranker was the cause.
            if not 0 <= index < len(chunks):
                logger.warning("reranker returned out-of-range index %s", index)
                continue
            reranked.append(
                RerankedChunk(chunk=chunks[index], relevance=float(result.relevance_score))
            )
        return reranked


class PassthroughReranker:
    """Used when Cohere is not configured: keeps the retrieval order.

    **Deliberately not a raising stand-in**, unlike the unconfigured vector
    client. The distinction is whether the feature can be honestly delivered
    without the dependency. Without a vector store there are no passages at
    all, so answering would mean inventing one. Without a reranker there are
    still real, relevant passages -- just ordered by embedding similarity
    rather than a cross-encoder. That is a *quality* reduction, not a
    correctness failure, and refusing to answer would be the worse outcome.

    Logged once at construction so a deployment running degraded knows it.
    """

    def __init__(self) -> None:
        logger.warning(
            "COHERE__API_KEY is not set -- answers will use embedding-ranked "
            "passages without cross-encoder reranking, which is measurably worse "
            "at putting the passage that answers the question first."
        )

    async def rerank(
        self, *, query: str, chunks: list[RetrievedChunk], top_n: int
    ) -> list[RerankedChunk]:
        del query
        # The chunk's own vector score, carried through unchanged. Not
        # normalized or rescaled to look like a relevance score: a caller
        # comparing these across configurations should see that they differ.
        return [RerankedChunk(chunk=c, relevance=c.score) for c in chunks[:top_n]]
