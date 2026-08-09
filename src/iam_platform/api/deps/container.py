"""Dependency container the ``api`` layer reads from ``request.app.state``.

Every field is typed against an ``application``/``core`` Protocol or plain
data type -- never a concrete ``infrastructure`` class -- so this module
(and everything in ``api/``) stays import-linter-clean against the
"API does not import infrastructure directly" contract in
docs/20-dependency-rules.md. The concrete objects are constructed and
assigned by ``iam_platform.bootstrap`` (the true composition root, which
sits outside the layered packages precisely so it's allowed to import all of
them) and handed to ``api.main.create_app`` already built.

Named ``AppContainer`` (not ``IdentityContainer``, its Phase 5 name) since
Phase 6 added tenancy/platform/tenant-authorization factories alongside the
identity ones -- it's the one container for the whole app, not per-module.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from iam_platform.application.ai_resources.ports import (
    AiResourceUowFactory,
    ChatModel,
    CrawlJobQueue,
    CredentialEncryptor,
    DocumentIngestionQueue,
    DocumentParser,
    ObjectStorageClient,
    ObjectStoragePathFactory,
    PublicWidgetLookup,
    Reranker,
    UrlValidator,
    VectorNamespaceFactory,
    VectorSearchClient,
    WidgetQuotaStore,
    WidgetSessionIssuer,
)
from iam_platform.application.identity.ports import (
    EmailSender,
    IdentityUnitOfWork,
    JwtIssuer,
    JwtVerifier,
    MfaChallengeStore,
    OAuthProvider,
    OAuthStateStore,
    PasswordHasher,
    RateLimiter,
    TotpService,
)
from iam_platform.application.ops.ports import HealthCheck
from iam_platform.application.platform_authz.ports import PlatformUowFactory
from iam_platform.application.tenancy.ports import InvitationEmailSender
from iam_platform.application.tenant_authz.ports import TenantUowFactory
from iam_platform.core.clock import Clock
from iam_platform.core.config import Settings


@dataclass(slots=True)
class AppContainer:
    # identity
    uow_factory: Callable[[], IdentityUnitOfWork]
    clock: Clock
    password_hasher: PasswordHasher
    jwt_issuer: JwtIssuer
    jwt_verifier: JwtVerifier
    totp_service: TotpService
    mfa_challenge_store: MfaChallengeStore
    rate_limiter: RateLimiter
    email_sender: EmailSender
    oauth_state_store: OAuthStateStore
    oauth_providers: dict[str, OAuthProvider]

    # tenancy / platform_authz / tenant_authz
    tenant_uow_factory: TenantUowFactory
    platform_uow_factory: PlatformUowFactory
    invitation_email_sender: InvitationEmailSender

    # ai_resources
    ai_resource_uow_factory: AiResourceUowFactory
    credential_encryptor: CredentialEncryptor
    vector_namespace_factory: VectorNamespaceFactory
    storage_path_factory: ObjectStoragePathFactory
    #: Writes the actual bytes. Distinct from `storage_path_factory`, which
    #: only decides *where* they go -- see the port docstrings.
    object_storage_client: ObjectStorageClient
    #: Used by the upload route only to *reject* unsupported types at the
    #: boundary. The worker constructs its own for actual parsing.
    document_parser: DocumentParser
    vector_search_client: VectorSearchClient
    document_ingestion_queue: DocumentIngestionQueue
    crawl_job_queue: CrawlJobQueue
    reranker: Reranker
    #: Public widget surface (Phase 13B). Typed as ports so `api` stays free of
    #: `infrastructure` imports (docs/20).
    public_widget_lookup: PublicWidgetLookup
    widget_quota: WidgetQuotaStore
    widget_token_service: WidgetSessionIssuer
    chat_model: ChatModel
    #: The SSRF guard, as a port -- `api` may not import `infrastructure`.
    url_validator: UrlValidator

    # ops
    health_check: HealthCheck
    #: Called by the app's lifespan on shutdown to release engines, the Redis
    #: connection pool, and the HTTP client. Typed as a bare callable rather
    #: than exposing the concrete resources, so this module still never names
    #: an ``infrastructure`` type (docs/20-dependency-rules.md).
    shutdown: Callable[[], Awaitable[None]]

    settings: Settings
