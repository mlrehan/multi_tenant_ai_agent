"""Which AI providers this platform can actually talk to, and what each supports.

**Only providers with a real adapter appear as supported here.** The
requirement to offer OpenAI, Gemini, Anthropic and xAI is met by modelling the
*vocabulary* of all four while marking as usable only the ones
`infrastructure/chat/` and `infrastructure/embeddings/` can genuinely serve --
today, OpenAI alone. Listing the other three as selectable would produce a
configuration a platform admin can save, assign to a tenant, and watch fail on
the first question, with the failure surfacing as a provider error rather than
"this platform has no adapter for that". A `supported=False` entry that is
refused at write time says so at the moment the operator can act on it.

Adding one is deliberately small: write the adapter, flip `supported`, and fill
in the capability row. Nothing else in the system branches on the provider.

**Capabilities exist so unsupported fields stay null rather than being faked.**
A provider with no embedding endpoint must not carry an `embedding_model`
someone might believe is in use, and a chat model that rejects a reasoning
parameter must not have one stored against it -- this codebase has already
been bitten twice by sending a parameter a model refuses (`temperature`, then
`reasoning_effort="minimal"`), each time failing every request while every test
passed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class AiProvider(StrEnum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"
    XAI = "xai"


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    provider: AiProvider
    #: False => this platform has no adapter. Configuration is refused.
    supported: bool
    supports_embeddings: bool
    #: Whether the embedding endpoint honours a requested output dimension.
    #: When false, `embedding_dimensions` is fixed by the model and must be
    #: left null rather than stored as a number nothing reads.
    supports_embedding_dimensions: bool
    supports_reasoning_effort: bool
    supports_request_timeout: bool
    label: str


_CAPABILITIES: dict[AiProvider, ProviderCapabilities] = {
    AiProvider.OPENAI: ProviderCapabilities(
        provider=AiProvider.OPENAI,
        supported=True,
        supports_embeddings=True,
        # `text-embedding-3-*` accepts a `dimensions` argument, and this
        # platform already relies on it: the same number sizes the Qdrant
        # collection and is requested from the API, so the index and the
        # vectors cannot desynchronise.
        supports_embedding_dimensions=True,
        supports_reasoning_effort=True,
        supports_request_timeout=True,
        label="OpenAI",
    ),
    AiProvider.ANTHROPIC: ProviderCapabilities(
        provider=AiProvider.ANTHROPIC,
        supported=False,
        supports_embeddings=False,
        supports_embedding_dimensions=False,
        supports_reasoning_effort=True,
        supports_request_timeout=True,
        label="Anthropic",
    ),
    AiProvider.GEMINI: ProviderCapabilities(
        provider=AiProvider.GEMINI,
        supported=False,
        supports_embeddings=True,
        supports_embedding_dimensions=True,
        supports_reasoning_effort=False,
        supports_request_timeout=True,
        label="Google Gemini",
    ),
    AiProvider.XAI: ProviderCapabilities(
        provider=AiProvider.XAI,
        supported=False,
        supports_embeddings=False,
        supports_embedding_dimensions=False,
        supports_reasoning_effort=True,
        supports_request_timeout=True,
        label="xAI (Grok)",
    ),
}


def capabilities_for(provider: AiProvider | str) -> ProviderCapabilities:
    return _CAPABILITIES[AiProvider(provider)]


def all_capabilities() -> list[ProviderCapabilities]:
    return list(_CAPABILITIES.values())


class UnsupportedProviderFieldError(ValueError):
    """A field was set that the chosen provider has no meaning for."""


def validate_provider_fields(
    *,
    provider: AiProvider | str,
    embedding_model: str | None,
    embedding_dimensions: int | None,
    chat_reasoning_effort: str | None,
    request_timeout_seconds: int | None,
) -> None:
    """Refuses configuration a provider cannot honour.

    Called on every write. The point is not tidiness: a stored
    `chat_reasoning_effort` against a model that rejects it produces a 400 from
    the provider on *every* question through that configuration, and the
    tenant sees a broken assistant with nothing in the console explaining why.
    Catching it at configuration time puts the error in front of the person who
    can fix it.
    """
    caps = capabilities_for(provider)
    if not caps.supported:
        raise UnsupportedProviderFieldError(
            f"{caps.label} is not yet implemented on this platform. "
            "Supported providers: "
            + ", ".join(c.label for c in all_capabilities() if c.supported)
        )
    if embedding_model and not caps.supports_embeddings:
        raise UnsupportedProviderFieldError(
            f"{caps.label} does not provide embeddings; leave the embedding model empty"
        )
    if embedding_dimensions is not None and not caps.supports_embedding_dimensions:
        raise UnsupportedProviderFieldError(
            f"{caps.label} does not accept a configurable embedding dimension"
        )
    if chat_reasoning_effort and not caps.supports_reasoning_effort:
        raise UnsupportedProviderFieldError(
            f"{caps.label} does not accept a reasoning effort"
        )
    if request_timeout_seconds is not None:
        if not caps.supports_request_timeout:
            raise UnsupportedProviderFieldError(
                f"{caps.label} does not accept a request timeout"
            )
        if not 1 <= request_timeout_seconds <= 600:
            raise UnsupportedProviderFieldError(
                "the request timeout must be between 1 and 600 seconds"
            )
    if embedding_dimensions is not None and not 1 <= embedding_dimensions <= 8192:
        raise UnsupportedProviderFieldError(
            "embedding dimensions must be between 1 and 8192"
        )
