"""The composition root -- the only module allowed to import from every layer.

Deliberately placed outside the ``api``/``application``/``domain``/
``infrastructure``/``core``/``workers`` packages (docs/20-dependency-rules.md):
those packages are constrained by the import-linter ``layers`` contract, and
a module that wires concrete infrastructure into the API's dependency
container has to import both ends. Putting that wiring in a sibling module
keeps the contract meaningful instead of needing an exemption carved into it.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

import httpx
from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from iam_platform.api.deps.container import AppContainer
from iam_platform.api.main import create_app
from iam_platform.application.identity.ports import OAuthProvider
from iam_platform.core.clock import SystemClock
from iam_platform.core.config import Settings
from iam_platform.infrastructure.cache.conversation_events import (
    RedisConversationEventPublisher,
)
from iam_platform.infrastructure.cache.mfa_challenge_store import RedisMfaChallengeStore
from iam_platform.infrastructure.cache.oauth_state_store import RedisOAuthStateStore
from iam_platform.infrastructure.cache.rate_limiter import RedisRateLimiter
from iam_platform.infrastructure.cache.redis_client import build_redis_client
from iam_platform.infrastructure.cache.tenant_quota import RedisTenantQuotaStore
from iam_platform.infrastructure.cache.token_usage import RedisTokenUsageStore
from iam_platform.infrastructure.cache.typing_indicator import RedisTypingIndicatorStore
from iam_platform.infrastructure.cache.widget_memory import RedisWidgetMemoryStore
from iam_platform.infrastructure.cache.widget_quota import RedisWidgetQuotaStore
from iam_platform.infrastructure.chat.openai_chat import (
    OpenAIChatModel,
    UnconfiguredChatModel,
)
from iam_platform.infrastructure.crawling.url_safety import UrlSafetyPolicy
from iam_platform.infrastructure.crawling.url_validator import SsrfUrlValidator
from iam_platform.infrastructure.db.repositories.ai_resources import SqlPublicWidgetLookup
from iam_platform.infrastructure.db.session import (
    build_engine,
    build_platform_engine,
    build_session_factory,
)
from iam_platform.infrastructure.db.unit_of_work import (
    SqlAiResourceUnitOfWork,
    SqlIdentityUnitOfWork,
    SqlPlatformUnitOfWork,
    SqlTenantUnitOfWork,
)
from iam_platform.infrastructure.email.console_sender import (
    ConsoleEmailSender,
    ConsoleInvitationEmailSender,
)
from iam_platform.infrastructure.factories import (
    build_object_storage_client,
    build_secret_provider,
    build_vector_stack,
)
from iam_platform.infrastructure.oauth.facebook import FacebookOAuthProvider
from iam_platform.infrastructure.oauth.google import GoogleOAuthProvider
from iam_platform.infrastructure.ops.health import DependencyHealthCheck
from iam_platform.infrastructure.parsing.dispatcher import ParserDispatcher
from iam_platform.infrastructure.push.web_push import build_web_push_sender
from iam_platform.infrastructure.queue.celery_ingestion_queue import (
    CeleryCrawlJobQueue,
    CeleryDocumentIngestionQueue,
)
from iam_platform.infrastructure.reranking.cohere_reranker import (
    CohereReranker,
    PassthroughReranker,
)
from iam_platform.infrastructure.secrets.resolver import resolve_secrets
from iam_platform.infrastructure.security.encryption import FernetCredentialEncryptor
from iam_platform.infrastructure.security.jwt_service import PyJwtService
from iam_platform.infrastructure.security.password_hasher import Argon2IdPasswordHasher
from iam_platform.infrastructure.security.totp import PyOtpTotpService
from iam_platform.infrastructure.security.widget_token import WidgetTokenService
from iam_platform.infrastructure.storage.paths import TenantScopedStoragePathFactory
from iam_platform.infrastructure.vector.namespaces import TenantScopedVectorNamespaceFactory

logger = logging.getLogger("iam_platform.bootstrap")


async def build_container(settings: Settings) -> AppContainer:
    # Resolve `secret://` references before anything reads a credential.
    # Async because a real provider does network I/O; see
    # docs/21-configuration-and-secrets.md's "Resolution order at startup".
    settings = await resolve_secrets(settings, build_secret_provider(settings))

    # `engine` is the RLS-subject app_tenant connection, shared by the
    # identity and tenant Units of Work (identity tables have no RLS to
    # apply, tenant tables rely on app_tenant genuinely being non-superuser --
    # see docs/18-schema-rls-and-migrations.md). `platform_engine` is the
    # separate BYPASSRLS app_platform connection, used only by
    # SqlPlatformUnitOfWork.
    engine = build_engine(settings.database)
    session_factory = build_session_factory(engine)
    platform_engine = build_platform_engine(settings.database)
    platform_session_factory = build_session_factory(platform_engine)

    redis = build_redis_client(settings.redis)
    http_client = httpx.AsyncClient(timeout=10.0)

    jwt_service = PyJwtService(settings.jwt)

    # Both now have real consumers (Phase 11's upload route writes bytes,
    # the Celery worker reads them), so they are wired rather than held back.
    vector_search_client, _embedding_client = build_vector_stack(settings)
    object_storage_client = build_object_storage_client(settings)
    # Built once and shared: the chat adapter needs it to decrypt a tenant's
    # own provider key at model-call time, which is the only place in the
    # system that ever sees one in plaintext.
    credential_encryptor = FernetCredentialEncryptor(
        settings.encryption.data_key.get_secret_value()
    )

    oauth_providers: dict[str, OAuthProvider] = {}
    if settings.oauth_google.enabled:
        oauth_providers["google"] = GoogleOAuthProvider(settings.oauth_google, http_client)
    if settings.oauth_facebook.enabled:
        oauth_providers["facebook"] = FacebookOAuthProvider(settings.oauth_facebook, http_client)

    return AppContainer(
        uow_factory=lambda: SqlIdentityUnitOfWork(session_factory),
        clock=SystemClock(),
        password_hasher=Argon2IdPasswordHasher(),
        jwt_issuer=jwt_service,
        jwt_verifier=jwt_service,
        totp_service=PyOtpTotpService(settings.mfa),
        mfa_challenge_store=RedisMfaChallengeStore(redis),
        rate_limiter=RedisRateLimiter(redis),
        email_sender=ConsoleEmailSender(),
        oauth_state_store=RedisOAuthStateStore(redis),
        oauth_providers=oauth_providers,
        tenant_uow_factory=lambda user_id, tenant_id: SqlTenantUnitOfWork(
            session_factory, user_id=user_id, tenant_id=tenant_id
        ),
        platform_uow_factory=lambda user_id: SqlPlatformUnitOfWork(
            platform_session_factory, user_id=user_id
        ),
        invitation_email_sender=ConsoleInvitationEmailSender(),
        ai_resource_uow_factory=lambda user_id, tenant_id: SqlAiResourceUnitOfWork(
            session_factory, user_id=user_id, tenant_id=tenant_id
        ),
        credential_encryptor=credential_encryptor,
        vector_namespace_factory=TenantScopedVectorNamespaceFactory(),
        storage_path_factory=TenantScopedStoragePathFactory(),
        vector_search_client=vector_search_client,
        object_storage_client=object_storage_client,
        document_parser=ParserDispatcher(),
        document_ingestion_queue=CeleryDocumentIngestionQueue(settings),
        crawl_job_queue=CeleryCrawlJobQueue(settings),
        # The public widget surface. `public_widget_lookup` runs on the
        # *platform* (BYPASSRLS) session factory by necessity: a visitor
        # supplies only a public key, so the tenant is what the lookup is
        # discovering and there is no RLS context to set yet.
        public_widget_lookup=SqlPublicWidgetLookup(platform_session_factory),
        widget_quota=RedisWidgetQuotaStore(redis),
        # TTL matched to the session token, so a visitor's memory cannot
        # outlive the credential that is allowed to read it.
        widget_memory=RedisWidgetMemoryStore(
            redis, ttl_seconds=settings.jwt.widget_session_ttl_seconds
        ),
        # Wired unconditionally, like the widget quota: an unconfigured
        # spending control is worse than none, because it looks configured.
        # No configuration knob: an indicator that silently did nothing would
        # be indistinguishable from nobody typing.
        typing_indicators=RedisTypingIndicatorStore(redis),
        token_usage=RedisTokenUsageStore(redis),
        tenant_quota=RedisTenantQuotaStore(redis),
        conversation_events=RedisConversationEventPublisher(redis),
        web_push=build_web_push_sender(settings.push),
        widget_token_service=WidgetTokenService(settings.jwt),
        # Cohere absent degrades ranking quality; OpenAI absent makes an answer
        # impossible. Hence one falls back and the other raises -- see the
        # docstrings on PassthroughReranker and UnconfiguredChatModel.
        reranker=(
            CohereReranker(settings.cohere)
            if settings.cohere.api_key.get_secret_value()
            else PassthroughReranker()
        ),
        chat_model=(
            OpenAIChatModel(settings.openai, credential_encryptor=credential_encryptor)
            if settings.openai.api_key.get_secret_value()
            else UnconfiguredChatModel()
        ),
        url_validator=SsrfUrlValidator(
            UrlSafetyPolicy(
                allow_private_network_targets=settings.crawl.allow_private_network_targets
            )
        ),
        health_check=DependencyHealthCheck(
            engine=engine, platform_engine=platform_engine, redis=redis
        ),
        shutdown=_build_shutdown(
            engine=engine,
            platform_engine=platform_engine,
            redis=redis,
            http_client=http_client,
        ),
        settings=settings,
    )


def _build_shutdown(
    *,
    engine: AsyncEngine,
    platform_engine: AsyncEngine,
    redis: Redis,
    http_client: httpx.AsyncClient,
) -> Callable[[], Awaitable[None]]:
    """Closes everything the container opened.

    Before Phase 9 nothing disposed these: both engines' connection pools, the
    Redis pool, and the OAuth HTTP client leaked on every shutdown. In a
    rolling deploy that means terminating pods hold Postgres connections open
    until the server times them out, so the new pods contend for a connection
    budget the old ones haven't released.

    Failures are logged and swallowed rather than raised -- a shutdown path
    that raises can leave the remaining resources unclosed, which is strictly
    worse than the error it was reporting.
    """

    async def shutdown() -> None:
        for name, close in (
            ("http_client", http_client.aclose()),
            ("redis", redis.aclose()),
            ("engine", engine.dispose()),
            ("platform_engine", platform_engine.dispose()),
        ):
            try:
                await close
            except Exception:
                logger.exception("failed to close %s during shutdown", name)

    return shutdown


async def build_app() -> FastAPI:
    # Settings' required fields (database, jwt) have no Python-level default --
    # pydantic-settings populates them from the environment/.env at
    # construction time, which mypy can't see from the class definition alone.
    settings = Settings()
    container = await build_container(settings)
    return create_app(container)
