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

**`model_name`/`model_parameters` are per-call overrides of the settings
above**, supplied only when `AnswerQuestion` is answering on behalf of a
caller-named assistant (see `answer_question.py`). Omitted, every call behaves
exactly as it did before these parameters existed -- the public widget and a
plain "ask this knowledge base" call never supply them.
"""

from __future__ import annotations

import hashlib
import logging
from collections import OrderedDict
from collections.abc import AsyncIterator
from typing import Any

from iam_platform.application.ai_resources.exceptions import (
    ProviderCredentialUnusableError,
)
from iam_platform.application.ai_resources.ports import (
    CredentialEncryptor,
    GroundingContext,
    TokenUsage,
)
from iam_platform.core.config import OpenAISettings

logger = logging.getLogger("iam_platform.infrastructure.chat")

#: How many distinct tenant keys keep a live connection pool. Bounded because
#: each entry is an HTTP client holding sockets, and the number of tenant
#: credentials is unbounded by design.
_MAX_CACHED_CLIENTS = 32


class OpenAIChatModel:
    def __init__(
        self,
        settings: OpenAISettings,
        *,
        client: Any | None = None,
        credential_encryptor: CredentialEncryptor | None = None,
    ) -> None:
        self._model = settings.chat_model
        self._temperature = settings.chat_temperature
        self._reasoning_effort = settings.chat_reasoning_effort
        self._encryptor = credential_encryptor
        # Keyed by a digest of the *ciphertext*, never the plaintext key: the
        # digest is stable for as long as the stored credential is, so rotating
        # a credential produces a new key and the stale client is evicted
        # rather than silently reused with the old secret.
        self._byok_clients: OrderedDict[str, Any] = OrderedDict()
        if client is not None:
            self._client: Any = client
            return

        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=settings.api_key.get_secret_value())

    async def _client_for(self, ciphertext: bytes) -> Any:
        """The client that bills the *tenant's* provider account.

        Cached, because building one per request means a fresh connection pool
        per request -- no keep-alive, and sockets accumulating faster than they
        are reclaimed under load.
        """
        if self._encryptor is None:
            raise ProviderCredentialUnusableError(
                "this deployment cannot use tenant-supplied provider credentials: "
                "no credential encryptor is configured"
            )
        digest = hashlib.sha256(ciphertext).hexdigest()
        cached = self._byok_clients.get(digest)
        if cached is not None:
            self._byok_clients.move_to_end(digest)
            return cached

        try:
            api_key = self._encryptor.decrypt(ciphertext)
        except Exception as exc:
            # The exception is logged, never the ciphertext or any part of the
            # key. A decrypt failure means the stored bytes do not match this
            # deployment's data key -- a rotation or restore problem an
            # operator must see, and one no retry will fix.
            logger.warning("provider credential could not be decrypted: %s", exc)
            raise ProviderCredentialUnusableError(
                "the provider credential on this model configuration could not be "
                "decrypted; it may need to be re-entered"
            ) from exc

        from openai import AsyncOpenAI

        created = AsyncOpenAI(api_key=api_key)
        self._byok_clients[digest] = created
        while len(self._byok_clients) > _MAX_CACHED_CLIENTS:
            _evicted_digest, evicted = self._byok_clients.popitem(last=False)
            try:
                await evicted.close()
            except Exception:  # pragma: no cover - best effort teardown
                logger.debug("could not close an evicted provider client")
        return created

    async def stream_answer(
        self,
        *,
        question: str,
        context: list[GroundingContext],
        system_prompt: str,
        model_name: str | None = None,
        model_parameters: dict[str, Any] | None = None,
        usage: TokenUsage | None = None,
        credential_ciphertext: bytes | None = None,
    ) -> AsyncIterator[str]:
        temperature = self._temperature
        reasoning_effort = self._reasoning_effort
        # `model_parameters` comes from a tenant-editable `ModelConfiguration`
        # row, so it is read defensively: a malformed value degrades to the
        # platform default rather than failing the whole answer, and only the
        # two keys this adapter already understands are recognised. Anything
        # else is silently ignored -- forward-compatible with a `parameters`
        # dict that later grows keys this adapter does not act on yet.
        if model_parameters:
            if "temperature" in model_parameters:
                try:
                    temperature = float(model_parameters["temperature"])
                except (TypeError, ValueError):
                    logger.warning(
                        "model configuration parameters.temperature is not a number; "
                        "using the platform default"
                    )
            if "reasoning_effort" in model_parameters:
                value = model_parameters["reasoning_effort"]
                if isinstance(value, str) and value:
                    reasoning_effort = value
                else:
                    logger.warning(
                        "model configuration parameters.reasoning_effort is not a "
                        "non-empty string; using the platform default"
                    )

        request: dict[str, Any] = {
            "model": model_name or self._model,
            "stream": True,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": _render_prompt(question, context)},
            ],
        }
        # Omitted entirely when unset -- passing `temperature=None` is not the
        # same as not passing it, and the models that reject the parameter
        # reject an explicit null too.
        if temperature is not None:
            request["temperature"] = temperature
        # Same opt-in shape, and the reason is the same: non-reasoning models
        # reject the parameter. See `OpenAISettings.chat_reasoning_effort` for
        # the measured distribution -- it cuts the *worst case* on this model
        # from ~10.8s to ~1.6s, which is most of what a visitor experiences as
        # "the chat is slow". The median moves far less.
        if reasoning_effort is not None:
            request["reasoning_effort"] = reasoning_effort
        # Asked for only when a caller wants the number, so an answer with no
        # budget behind it sends the identical request it always did. Verified
        # against the live API before being relied on -- `temperature` taught
        # this codebase not to assume a parameter is accepted.
        if usage is not None:
            request["stream_options"] = {"include_usage": True}

        client = self._client
        if credential_ciphertext is not None:
            client = await self._client_for(credential_ciphertext)

        try:
            stream = await client.chat.completions.create(**request)
        except Exception as exc:
            # Only reinterpreted when the tenant's *own* key was used. The same
            # 401 from the platform's key is a deployment fault, and telling a
            # tenant admin to fix a credential they do not own would send them
            # somewhere they can change nothing.
            if credential_ciphertext is not None and _is_auth_rejection(exc):
                logger.warning("tenant provider credential rejected: %s", type(exc).__name__)
                raise ProviderCredentialUnusableError(
                    "the provider rejected this model configuration's credential; "
                    "it may be expired, revoked at the provider, or lack access to "
                    "the requested model"
                ) from exc
            raise

        async for event in stream:
            # The usage-bearing chunk arrives last and carries *no* choices,
            # so it has to be read before the `continue` below -- which is
            # exactly where it would otherwise be discarded unnoticed.
            reported = getattr(event, "usage", None)
            if usage is not None and reported is not None:
                usage.total = int(getattr(reported, "total_tokens", 0) or 0)
            if not event.choices:
                continue
            delta = event.choices[0].delta
            piece = getattr(delta, "content", None)
            if piece:
                yield piece


def _is_auth_rejection(exc: BaseException) -> bool:
    """Did the provider refuse the *key*, as opposed to the request?

    Matched on status code rather than on `isinstance(exc, openai.Authentication
    Error)` so that this module does not import the SDK's exception tree at
    call time -- and so a provider-compatible gateway that raises its own
    exception type is still recognised. 401 is a bad key; 403 is a key without
    access to the requested model. Both are the same instruction to the tenant
    admin: fix the credential on this configuration.
    """
    return getattr(exc, "status_code", None) in (401, 403)


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
        self,
        *,
        question: str,
        context: list[GroundingContext],
        system_prompt: str,
        model_name: str | None = None,
        model_parameters: dict[str, Any] | None = None,
        usage: TokenUsage | None = None,
        credential_ciphertext: bytes | None = None,
    ) -> AsyncIterator[str]:
        raise RuntimeError(
            "answer generation is not configured: set OPENAI__API_KEY. Refusing "
            "to produce an answer rather than emitting a plausible-looking "
            "'no information available', which would be indistinguishable from "
            "a real answer and invisible in logs."
        )
