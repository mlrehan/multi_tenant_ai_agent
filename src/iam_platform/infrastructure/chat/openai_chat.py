"""`ChatModel` backed by OpenAI chat completions, streamed.

**The context is assembled here, not by the caller.** The pipeline decides
*which* passages ground an answer; this module decides how they are presented
to the model. Keeping that split means the prompt format can change -- labels,
ordering, delimiters -- without touching retrieval, and the pipeline's tests do
not depend on prompt wording.

**Temperature is sent only if configured.** Zero is the right value for
grounded answering -- variation there is paraphrase drift away from the source,
the exact failure grounding exists to prevent -- but newer OpenAI models reject
*any* explicit temperature and accept only their default. Hardcoding 0 made
every answer fail with a 400 against the model this deployment actually uses.
So `OPENAI__CHAT_TEMPERATURE` is opt-in: set it on a model that honours it,
leave it unset otherwise.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from iam_platform.application.ai_resources.ports import GroundingContext
from iam_platform.core.config import OpenAISettings

logger = logging.getLogger("iam_platform.infrastructure.chat")


class OpenAIChatModel:
    def __init__(self, settings: OpenAISettings, *, client: Any | None = None) -> None:
        self._model = settings.chat_model
        self._temperature = settings.chat_temperature
        if client is not None:
            self._client: Any = client
            return

        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=settings.api_key.get_secret_value())

    async def stream_answer(
        self, *, question: str, context: list[GroundingContext], system_prompt: str
    ) -> AsyncIterator[str]:
        request: dict[str, Any] = {
            "model": self._model,
            "stream": True,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": _render_prompt(question, context)},
            ],
        }
        # Omitted entirely when unset -- passing `temperature=None` is not the
        # same as not passing it, and the models that reject the parameter
        # reject an explicit null too.
        if self._temperature is not None:
            request["temperature"] = self._temperature

        stream = await self._client.chat.completions.create(**request)
        async for event in stream:
            if not event.choices:
                continue
            delta = event.choices[0].delta
            piece = getattr(delta, "content", None)
            if piece:
                yield piece


def _render_prompt(question: str, context: list[GroundingContext]) -> str:
    """Passages first, question last.

    Deliberate ordering: a model that has read the question before the sources
    is more prone to going looking for support for an answer it has already
    formed. Sources first, then the question asked of them.

    Each passage is fenced and labelled. The label is what the model is told to
    cite by, and the fence is what stops a passage that happens to contain
    something like `[1]` or an instruction being read as part of the prompt --
    the crawled web is full of text that looks like both.
    """
    blocks = "\n\n".join(
        f"<<<SOURCE {item.label}>>>\n{item.text}\n<<<END {item.label}>>>"
        for item in context
    )
    return (
        f"Sources:\n\n{blocks}\n\n"
        f"Question: {question}"
    )


class UnconfiguredChatModel:
    """Raises rather than answering when no chat model is configured.

    The same reasoning as `UnconfiguredVectorSearchClient`: silence that looks
    like an answer is worse than an error. A stand-in that yielded "I don't
    know" would be indistinguishable, to a visitor and in the logs, from a
    knowledge base that genuinely lacks the information -- and this one is a
    deployment mistake someone needs to see.
    """

    def stream_answer(
        self, *, question: str, context: list[GroundingContext], system_prompt: str
    ) -> AsyncIterator[str]:
        raise RuntimeError(
            "answer generation is not configured: set OPENAI__API_KEY. Refusing "
            "to produce an answer rather than emitting a plausible-looking "
            "'no information available', which would be indistinguishable from "
            "a real answer and invisible in logs."
        )
