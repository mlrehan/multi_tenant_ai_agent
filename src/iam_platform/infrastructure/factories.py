"""Settings → adapter selection, shared by the API and worker composition roots.

These three builders answer "which concrete adapter does this configuration
mean?" -- the kind of decision that must have exactly one home, or a new
storage backend gets wired into one process and silently not the other.

**Why they live here and not in a composition root.** They started in
``iam_platform/bootstrap.py``, and ``workers/bootstrap.py`` imported them from
there. That looked harmless -- ``bootstrap.py`` is a top-level sibling outside
the layered packages -- but import-linter caught it: ``bootstrap.py`` imports
``api.main`` and ``api.deps.container`` to build the FastAPI container, so
importing *anything* from it dragged the whole ``api`` package into the worker
process's import graph, violating the ``workers`` ↛ ``api`` layering rule.
That is not a pedantic failure. A worker importing the API package means every
router, dependency and middleware is constructed at worker start, and a future
edit to an API module could break the worker for reasons no one would think to
look for.

Sitting in ``infrastructure`` instead, both composition roots import downward
into the layer whose adapters they are choosing between, and the contract
stays meaningful.
"""

from __future__ import annotations

import logging

from iam_platform.application.ai_resources.ports import (
    EmbeddingClient,
    ObjectStorageClient,
    VectorSearchClient,
)
from iam_platform.core.config import Settings
from iam_platform.infrastructure.embeddings.openai_client import OpenAIEmbeddingClient
from iam_platform.infrastructure.secrets.aws_secrets_manager import AwsSecretsManagerProvider
from iam_platform.infrastructure.secrets.base import SecretProvider
from iam_platform.infrastructure.secrets.env_provider import EnvSecretProvider
from iam_platform.infrastructure.storage.cloudflare_r2 import CloudflareR2StorageClient
from iam_platform.infrastructure.storage.local_filesystem import LocalFilesystemStorageClient
from iam_platform.infrastructure.vector.qdrant_search import QdrantVectorSearchClient
from iam_platform.infrastructure.vector.unconfigured import UnconfiguredVectorSearchClient

logger = logging.getLogger("iam_platform.infrastructure.factories")


def build_secret_provider(settings: Settings) -> SecretProvider:
    """Selects the ``SecretProvider`` named by ``SECRET_PROVIDER``.

    Only ``env`` and ``aws_secrets_manager`` are implemented. The other three
    values ``Settings`` accepts (vault, azure_key_vault, gcp_secret_manager)
    are the same three-method shape and are left for whichever the deploying
    organisation actually uses -- raising here is better than silently falling
    back to ``env``, which would put production secrets in plain environment
    variables (docs/21-configuration-and-secrets.md).
    """
    if settings.secret_provider == "env":
        return EnvSecretProvider()
    if settings.secret_provider == "aws_secrets_manager":
        return AwsSecretsManagerProvider(region_name=settings.aws_region)
    raise NotImplementedError(
        f"SECRET_PROVIDER={settings.secret_provider} is declared in Settings but has no "
        "adapter yet -- implement it in infrastructure/secrets/ before deploying with it"
    )


def build_object_storage_client(settings: Settings) -> ObjectStorageClient:
    """Selects the storage backend named by ``STORAGE__MODE``.

    An explicit switch, not an inference from whether R2 credentials happen to
    be present: silently writing tenant documents to a container's local disk
    because a key was missing is a data-loss bug that reports success.
    ``StorageSettings`` already refuses to construct in ``r2`` mode without
    credentials, so by the time this runs the choice is unambiguous.
    """
    if settings.storage.mode == "local":
        return LocalFilesystemStorageClient(settings.storage.local_path)
    return CloudflareR2StorageClient(settings.storage)


def build_vector_stack(
    settings: Settings,
) -> tuple[VectorSearchClient, EmbeddingClient | None]:
    """Builds the embedding + vector-search pair, or a loud stand-in.

    Knowledge-base search is optional: a deployment running this purely for
    identity and authorization has no reason to hold an OpenAI key, and the
    API must boot and serve normally without one. So an absent key disables
    the feature rather than blocking startup.

    It disables it *loudly*. Substituting the in-memory client would answer
    every search with an empty result set -- indistinguishable from a
    knowledge base that genuinely has no matches, and invisible in the logs.
    ``UnconfiguredVectorSearchClient`` raises instead, naming the setting that
    would fix it (see its module docstring).

    Neither real client opens a connection here: the OpenAI and Qdrant SDKs
    are lazy, so a vector store that happens to be down delays failure to
    first use rather than preventing the API from starting.
    """
    if not settings.openai.api_key.get_secret_value():
        logger.warning(
            "OPENAI__API_KEY is not set -- knowledge-base ingestion and search are "
            "disabled; the API will serve identity and authorization normally"
        )
        return UnconfiguredVectorSearchClient(), None

    embedding_client = OpenAIEmbeddingClient(settings.openai)
    return QdrantVectorSearchClient(settings.qdrant, embedding_client), embedding_client
