"""OpenAI ``EmbeddingClient`` -- ``text-embedding-3-large`` by default.

The ``dimensions`` value is *requested from the API*, not merely recorded:
text-embedding-3 models support returning a shortened vector, so passing the
configured number means the vector this client produces and the Qdrant
collection sized from ``self.dimensions`` are guaranteed to agree. A
model→size lookup table would be a second source of truth that a model change
could silently invalidate.

**Ordering.** The embeddings API returns objects carrying an ``index``, and
this sorts by it rather than trusting arrival order. The API does return them
in order in practice, but callers zip these vectors against their chunk list,
so if that ever stopped holding, every chunk would be indexed under another
chunk's embedding -- a corruption that produces plausible-looking but subtly
wrong search results rather than an error anyone would notice.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from iam_platform.application.ai_resources.ports import TokenUsage

if TYPE_CHECKING:
    from iam_platform.core.config import OpenAISettings

#: OpenAI accepts at most 2048 inputs per embeddings request. Batching below
#: that bound keeps a large document's chunk list from failing wholesale.
_MAX_INPUTS_PER_REQUEST = 2048


class OpenAIEmbeddingClient:
    def __init__(self, settings: OpenAISettings, *, client: Any | None = None) -> None:
        self._model = settings.embedding_model
        self._dimensions = settings.embedding_dimensions

        if client is not None:
            # Injectable for tests -- exercises batching and ordering logic
            # without network access or an API key.
            self._client: Any = client
            return

        if not settings.api_key.get_secret_value():
            # Fail here, with the setting's name, rather than letting an empty
            # key reach OpenAI and come back as a generic 401 from deep inside
            # an ingestion worker.
            raise RuntimeError(
                "OPENAI__API_KEY is not set -- required for knowledge-base embedding"
            )

        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(
            api_key=settings.api_key.get_secret_value(),
            timeout=settings.request_timeout_seconds,
        )

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(
        self, text: str, *, usage: TokenUsage | None = None
    ) -> list[float]:
        vectors = await self.embed_batch([text], usage=usage)
        return vectors[0]

    async def embed_batch(
        self, texts: list[str], *, usage: TokenUsage | None = None
    ) -> list[list[float]]:
        if not texts:
            # Short-circuit: the API rejects an empty input array, and "embed
            # nothing" is a legitimate thing for a caller to ask when a
            # document parsed to zero chunks.
            return []

        vectors: list[list[float]] = []
        for start in range(0, len(texts), _MAX_INPUTS_PER_REQUEST):
            batch = texts[start : start + _MAX_INPUTS_PER_REQUEST]
            response = await self._client.embeddings.create(
                model=self._model,
                input=batch,
                dimensions=self._dimensions,
            )
            ordered = sorted(response.data, key=lambda item: item.index)
            vectors.extend([float(value) for value in item.embedding] for item in ordered)

            # Accumulated per batch, not per call: a long document is several
            # requests, and only the sum is the real cost.
            #
            # Counted as **input** tokens, because that is what they are -- an
            # embedding has no completion. Reporting them as output would make
            # the input/output split on the console meaningless.
            if usage is not None:
                reported = getattr(response, "usage", None)
                if reported is not None:
                    spent = int(getattr(reported, "prompt_tokens", 0) or 0)
                    usage.input_tokens += spent
                    usage.total += spent

        return vectors
