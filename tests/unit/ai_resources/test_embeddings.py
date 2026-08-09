"""OpenAI embedding client: batching, ordering, and the dimensions contract.

Exercised against an injected fake rather than the real API -- these tests are
about *this adapter's* logic (splitting oversized batches, restoring order,
passing the configured dimension through), not about OpenAI's behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from iam_platform.core.config import OpenAISettings
from iam_platform.infrastructure.embeddings.openai_client import (
    _MAX_INPUTS_PER_REQUEST,
    OpenAIEmbeddingClient,
)

pytestmark = pytest.mark.unit


@dataclass
class _FakeEmbeddingItem:
    index: int
    embedding: list[float]


@dataclass
class _FakeEmbeddingResponse:
    data: list[_FakeEmbeddingItem]


class _FakeEmbeddings:
    def __init__(self, *, reverse_order: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self._reverse_order = reverse_order

    async def create(
        self, *, model: str, input: list[str], dimensions: int
    ) -> _FakeEmbeddingResponse:
        self.calls.append({"model": model, "input": input, "dimensions": dimensions})
        # One deterministic vector per input, derived from its position so a
        # mis-ordering is detectable.
        items = [
            _FakeEmbeddingItem(index=i, embedding=[float(i)] * dimensions)
            for i in range(len(input))
        ]
        if self._reverse_order:
            items.reverse()
        return _FakeEmbeddingResponse(data=items)


class _FakeOpenAIClient:
    def __init__(self, *, reverse_order: bool = False) -> None:
        self.embeddings = _FakeEmbeddings(reverse_order=reverse_order)


def _settings(**overrides: Any) -> OpenAISettings:
    # Small default dimension keeps the fake's vectors readable; `overrides`
    # must be able to replace it, hence setdefault rather than a keyword.
    overrides.setdefault("embedding_dimensions", 4)
    return OpenAISettings(**overrides)


class TestOpenAIEmbeddingClient:
    async def test_embed_batch_returns_one_vector_per_input(self) -> None:
        fake = _FakeOpenAIClient()
        client = OpenAIEmbeddingClient(_settings(), client=fake)

        vectors = await client.embed_batch(["a", "b", "c"])

        assert len(vectors) == 3
        assert all(len(v) == 4 for v in vectors)

    async def test_embed_batch_restores_input_order(self) -> None:
        """The whole reason the adapter sorts by `index`.

        Callers zip these vectors against their chunk list, so a reordered
        response would attach every embedding to the wrong text -- producing
        plausible-but-wrong search results rather than a visible error.
        """
        fake = _FakeOpenAIClient(reverse_order=True)
        client = OpenAIEmbeddingClient(_settings(), client=fake)

        vectors = await client.embed_batch(["a", "b", "c"])

        # Fake encodes position into the vector, so correct ordering is 0,1,2.
        assert [v[0] for v in vectors] == [0.0, 1.0, 2.0]

    async def test_embed_single_returns_one_vector(self) -> None:
        client = OpenAIEmbeddingClient(_settings(), client=_FakeOpenAIClient())

        vector = await client.embed("hello")

        assert len(vector) == 4

    async def test_empty_input_makes_no_api_call(self) -> None:
        """A document that parsed to zero chunks is legitimate; the embeddings
        API rejects an empty input array, so this must short-circuit."""
        fake = _FakeOpenAIClient()
        client = OpenAIEmbeddingClient(_settings(), client=fake)

        assert await client.embed_batch([]) == []
        assert fake.embeddings.calls == []

    async def test_oversized_input_is_split_across_requests(self) -> None:
        """OpenAI caps inputs per request; a large document must not fail
        wholesale because its chunk list exceeded that bound."""
        fake = _FakeOpenAIClient()
        client = OpenAIEmbeddingClient(_settings(), client=fake)
        texts = [f"chunk-{i}" for i in range(_MAX_INPUTS_PER_REQUEST + 10)]

        vectors = await client.embed_batch(texts)

        assert len(vectors) == len(texts)
        assert len(fake.embeddings.calls) == 2
        assert len(fake.embeddings.calls[0]["input"]) == _MAX_INPUTS_PER_REQUEST
        assert len(fake.embeddings.calls[1]["input"]) == 10

    async def test_configured_dimensions_are_requested_from_the_api(self) -> None:
        """The collection is sized from `client.dimensions`, so the API must be
        asked for that same number -- otherwise the vector store and the
        vectors disagree."""
        fake = _FakeOpenAIClient()
        client = OpenAIEmbeddingClient(_settings(embedding_dimensions=1536), client=fake)

        await client.embed_batch(["a"])

        assert client.dimensions == 1536
        assert fake.embeddings.calls[0]["dimensions"] == 1536

    async def test_configured_model_is_used(self) -> None:
        fake = _FakeOpenAIClient()
        client = OpenAIEmbeddingClient(
            _settings(embedding_model="text-embedding-3-small"), client=fake
        )

        await client.embed_batch(["a"])

        assert fake.embeddings.calls[0]["model"] == "text-embedding-3-small"

    def test_missing_api_key_fails_loudly_at_construction(self) -> None:
        """Rather than surfacing as an opaque 401 from inside a worker."""
        with pytest.raises(RuntimeError, match="OPENAI__API_KEY is not set"):
            OpenAIEmbeddingClient(_settings())
