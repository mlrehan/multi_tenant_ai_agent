"""Typed, environment-based configuration.

See docs/21-configuration-and-secrets.md for the full design. Values here are
read directly from the process environment (12-factor style); anything
prefixed ``secret://`` is a *reference* resolved by an
``infrastructure.secrets.SecretProvider`` at the composition root
(``bootstrap.py``), not inside this module -- ``core`` must not depend on
``infrastructure`` (docs/20-dependency-rules.md).

Nested settings groups are plain ``BaseModel`` classes, not ``BaseSettings``:
pydantic-settings resolves nested env vars (``DATABASE__HOST`` etc.) via the
root model's ``env_nested_delimiter`` using the *field name* as prefix -- a
nested class that is itself a ``BaseSettings`` with its own ``env_prefix``
does NOT get auto-populated by the parent (verified empirically; it raises
"field required" even when the prefixed env vars are set), so the nested
groups are deliberately plain ``BaseModel``.
"""

from __future__ import annotations

import os
from typing import Any, Literal

from dotenv import dotenv_values
from pydantic import BaseModel, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseModel):
    host: str = "localhost"
    port: int = 5432
    name: str = "iam_platform"

    # Application runtime connection -- RLS-subject (docs/18-schema-rls-and-migrations.md).
    user: str = "app_tenant"
    password: SecretStr

    # BYPASSRLS connection, used only by the Platform Service Layer
    # (docs/04-architecture-overview.md, docs/18-schema-rls-and-migrations.md)
    # -- never the default connection for a route handler.
    platform_user: str = "app_platform"
    platform_password: SecretStr

    # Table-owning role migrations run as -- distinct from both of the above
    # so a compromised app connection can never run DDL.
    migrator_user: str = "postgres"
    migrator_password: SecretStr

    pool_size: int = 10
    pool_max_overflow: int = 20

    def _dsn(self, user: str, password: SecretStr) -> str:
        return (
            f"postgresql+asyncpg://{user}:{password.get_secret_value()}"
            f"@{self.host}:{self.port}/{self.name}"
        )

    @property
    def async_dsn(self) -> str:
        return self._dsn(self.user, self.password)

    @property
    def platform_dsn(self) -> str:
        return self._dsn(self.platform_user, self.platform_password)

    @property
    def migrator_dsn(self) -> str:
        return self._dsn(self.migrator_user, self.migrator_password)


class RedisSettings(BaseModel):
    url: SecretStr = SecretStr("redis://localhost:6379/0")


class JwtSettings(BaseModel):
    issuer: str = "https://auth.example.invalid"
    audience: str = "iam-platform-api"
    private_key_pem: SecretStr
    public_key_pem: str
    algorithm: Literal["RS256", "EdDSA"] = "RS256"
    access_token_ttl_seconds: int = 900
    clock_skew_seconds: int = 30

    #: Audience for public chat-widget session tokens. **Deliberately distinct
    #: from `audience`, and that difference is a security boundary, not
    #: cosmetic.** `JwtService.verify` pins `audience=`, so a token minted for
    #: this audience is rejected outright by every authenticated endpoint --
    #: a website visitor cannot become a console user by presenting the token
    #: they were legitimately given. The same signing key is reused because the
    #: separation that matters is the audience claim, and a second keypair
    #: would double the rotation surface for no additional guarantee.
    widget_audience: str = "iam-platform-widget"

    #: Short: a visitor's session lasts a conversation, not a working day, and
    #: the token is handed to a browser on a public page.
    widget_session_ttl_seconds: int = 1800

    @field_validator("public_key_pem", mode="before")
    @classmethod
    def _normalize_public_key_newlines(cls, value: str) -> str:
        # PEM keys are commonly stored in env vars/secret managers as a single
        # line with literal "\n" escapes (real newlines don't survive most env
        # var transports cleanly) -- normalize back to actual newlines here so
        # the PEM parser sees a well-formed key regardless of how it arrived.
        return value.replace("\\n", "\n")

    @field_validator("private_key_pem", mode="before")
    @classmethod
    def _normalize_private_key_newlines(cls, value: str) -> str:
        return value.replace("\\n", "\n")


class OAuthProviderSettings(BaseModel):
    client_id: str = ""
    client_secret: SecretStr = SecretStr("")
    redirect_uri: str = ""
    enabled: bool = False


class PasswordPolicySettings(BaseModel):
    min_length: int = 12
    max_length: int = 256


class LockoutSettings(BaseModel):
    max_failed_attempts: int = 5
    window_minutes: int = 15
    lockout_minutes: int = 15


class MfaSettings(BaseModel):
    totp_issuer: str = "IAM Platform"


class RateLimitSettings(BaseModel):
    """Coarse per-IP edge limit -- distinct from the per-account login
    throttling in ``LockoutSettings``. See ``api/middleware/rate_limit.py``."""

    requests_per_window: int = 300
    window_seconds: int = 60


class EncryptionSettings(BaseModel):
    """Envelope encryption for provider credentials -- docs/16-schema-ai-resources.md.

    ``data_key`` is a urlsafe-base64 32-byte key. In development it comes from
    the environment like everything else; in staging/production it must be a
    ``secret://`` reference resolved through the ``SecretProvider`` at the
    composition root, and the real deployment wraps it with a KMS-managed key
    (the "envelope" half) rather than holding a bare key at all. That KMS
    integration is deferred (Phase 7 scope note) -- the port boundary is here
    so adding it doesn't change any caller.
    """

    data_key: SecretStr


class StorageSettings(BaseModel):
    """Where uploaded document bytes actually live.

    ``mode`` picks the adapter the same way ``secret_provider`` picks a
    ``SecretProvider``: an explicit switch, not an inference from whether
    credentials happen to be present. Silently falling back to local disk
    because an R2 key was missing would mean a production deploy writing
    tenant documents onto an ephemeral container filesystem and reporting
    success -- exactly the "designed but inert" failure mode Phase 9 found
    three of (docs/22-deployment-and-operations.md).
    """

    mode: Literal["local", "r2"] = "local"

    #: Only read when mode="local". Relative paths resolve against the process
    #: working directory; gitignored, since it holds real tenant uploads.
    local_path: str = "var/storage"

    #: Only read when mode="r2". R2 is S3-compatible, so this is plain boto3
    #: against a Cloudflare endpoint -- no R2-specific SDK.
    r2_account_id: str = ""
    r2_bucket: str = ""
    r2_access_key_id: SecretStr = SecretStr("")
    r2_secret_access_key: SecretStr = SecretStr("")

    @property
    def r2_endpoint_url(self) -> str:
        return f"https://{self.r2_account_id}.r2.cloudflarestorage.com"

    @model_validator(mode="after")
    def _require_r2_credentials_when_selected(self) -> StorageSettings:
        if self.mode != "r2":
            return self
        missing = [
            name
            for name, value in (
                ("STORAGE__R2_ACCOUNT_ID", self.r2_account_id),
                ("STORAGE__R2_BUCKET", self.r2_bucket),
                ("STORAGE__R2_ACCESS_KEY_ID", self.r2_access_key_id.get_secret_value()),
                ("STORAGE__R2_SECRET_ACCESS_KEY", self.r2_secret_access_key.get_secret_value()),
            )
            if not value
        ]
        if missing:
            raise ValueError(
                f"STORAGE__MODE=r2 requires {', '.join(missing)} -- refusing to start rather "
                "than fall back to local disk, which would lose every uploaded document"
            )
        return self


class QdrantSettings(BaseModel):
    """Vector store connection. One collection per tenant, named from the
    ``knowledge_bases.vector_namespace`` value the database already stores --
    the vector store mirrors the Postgres tenant-isolation model rather than
    inventing a parallel one (docs/24-knowledge-base-ingestion-and-rag.md).

    Deliberately *not* part of the ``/readyz`` dependency set: the IAM API
    serves authentication and authorization traffic perfectly well with the
    vector store down, and a readiness probe that fails on it would pull every
    pod out of rotation over a degraded RAG feature.
    """

    url: str = "http://localhost:6333"
    api_key: SecretStr = SecretStr("")
    timeout_seconds: float = 30.0


class OpenAISettings(BaseModel):
    """Embeddings and chat completion.

    ``embedding_dimensions`` is passed to the embeddings API *and* used as the
    Qdrant collection's vector size, so the two cannot drift apart: there is
    one number, not a model-implied value and a separately-configured index
    width that a careless edit could desynchronise.
    """

    api_key: SecretStr = SecretStr("")
    embedding_model: str = "text-embedding-3-large"
    embedding_dimensions: int = 3072
    chat_model: str = "gpt-5.5"

    #: Sent only when set. `None` means "use the model's default", which is
    #: required rather than merely convenient: newer OpenAI models **reject**
    #: any explicit temperature, returning
    #: `Unsupported value: 'temperature' does not support 0 with this model`.
    #: Hardcoding 0 -- which is otherwise right for grounded answering, where
    #: variation is paraphrase drift away from the source -- made every answer
    #: fail against the configured model. Found by running it, not by reading
    #: the API docs.
    chat_temperature: float | None = None
    request_timeout_seconds: float = 60.0


class CohereSettings(BaseModel):
    """Reranking of retrieved candidates (step 3 of the query pipeline in
    ``Architectural_Diagram.txt``)."""

    api_key: SecretStr = SecretStr("")
    rerank_model: str = "rerank-v3.5"


class TavilySettings(BaseModel):
    api_key: SecretStr = SecretStr("")


class IngestionSettings(BaseModel):
    """Chunking parameters for the ingestion pipeline.

    Defaults come from ``Architectural_Diagram.txt`` (700 tokens, 100 overlap).
    Configurable rather than hardcoded because the right values depend on the
    corpus, and re-tuning them shouldn't require a code change.
    """

    chunk_tokens: int = 700
    chunk_overlap_tokens: int = 100
    #: Token encoding used to measure chunk size; must match what the
    #: embedding model actually uses or chunks will be mis-sized.
    tokenizer_encoding: str = "cl100k_base"


class CrawlSettings(BaseModel):
    """Bounds on URL and website ingestion (Phase 12).

    Every one of these is a *cost and availability* control, not a tuning knob.
    "Crawl this entire website" is an instruction a tenant can give in three
    seconds that this platform then pays for in embedding calls, worker hours
    and outbound bandwidth -- against a site whose size the tenant may not know
    either. Unbounded, one crawl of a large wiki or a calendar with infinite
    pagination can consume a worker indefinitely and run up a real bill.

    Defaults were agreed with the platform owner rather than guessed:
    depth 3 covers a documentation or help site without descending into
    pagination and archives; 500 pages bounds the spend; 30s per page is
    generous for a JS-rendered page and short enough that a hanging one does
    not stall the queue; a crawl still running after 2 hours is stuck, not
    thorough.
    """

    max_depth: int = 3
    max_pages: int = 500
    page_timeout_seconds: int = 30
    job_timeout_seconds: int = 7200

    #: Crawling other people's sites from this platform's infrastructure is
    #: done politely by default. A tenant crawling their *own* site behind a
    #: restrictive robots.txt is the case for turning it off, deliberately.
    respect_robots_txt: bool = True

    #: Pages larger than this are skipped rather than parsed. A 200 MB "page"
    #: is a download link or a generated dump, not content worth indexing.
    max_page_bytes: int = 10 * 1024 * 1024

    #: Allows a deployment to relax the SSRF guard for a genuinely internal
    #: crawl target (a wiki on the same private network). Off by default: the
    #: worker sits inside this platform's network, so an unguarded crawler is a
    #: request-forgery primitive handed to any tenant admin. See
    #: `infrastructure/crawling/url_safety.py`.
    allow_private_network_targets: bool = False

    @model_validator(mode="after")
    def _reject_nonsense_bounds(self) -> CrawlSettings:
        if self.max_depth < 0:
            raise ValueError("CRAWL__MAX_DEPTH must not be negative")
        if self.max_pages < 1:
            raise ValueError("CRAWL__MAX_PAGES must be at least 1")
        if self.page_timeout_seconds < 1:
            raise ValueError("CRAWL__PAGE_TIMEOUT_SECONDS must be at least 1")
        if self.job_timeout_seconds <= self.page_timeout_seconds:
            # Otherwise the job budget cannot accommodate even one page, and
            # every crawl would time out having fetched nothing -- a
            # configuration that looks plausible and can never succeed.
            raise ValueError(
                "CRAWL__JOB_TIMEOUT_SECONDS must exceed CRAWL__PAGE_TIMEOUT_SECONDS"
            )
        return self


def _reject_near_miss_group_names(
    settings_cls: type[BaseSettings], env_file: str | os.PathLike[str] | None
) -> None:
    """Fails loudly on ``OPENAI_API_KEY`` when ``OPENAI__API_KEY`` was meant.

    ``extra="forbid"`` catches a name that matches nothing -- set
    ``TOTALLY_BOGUS=x`` and the model refuses to build. It does **not** catch a
    near miss. pydantic-settings claims any key whose lowercased name starts
    with a nested field's name (``openai``), so ``OPENAI_API_KEY`` is treated
    as handled by the ``openai`` field; but the nested delimiter is ``__``, so
    the remainder never resolves to ``api_key`` and the value evaporates. No
    error, no value -- the worst of both.

    That is not hypothetical: it is how this platform ran with knowledge-base
    ingestion silently disabled while the operator had, as far as they could
    tell, configured the key. The single-underscore form is also the name
    OpenAI's own SDK uses, so it is the *likely* mistake, not an exotic one.

    Checks the process environment and the dotenv file, since pydantic-settings
    reads the latter directly rather than exporting it.
    """
    group_names = {
        name
        for name, field in settings_cls.model_fields.items()
        if isinstance(field.annotation, type) and issubclass(field.annotation, BaseModel)
    }
    if not group_names:
        return

    candidates: set[str] = set(os.environ)
    if env_file and os.path.isfile(env_file):
        candidates |= set(dotenv_values(env_file))

    def _is_near_miss(key: str) -> bool:
        lowered = key.lower()
        # A key that *is* a field name is fine; only a single-underscore
        # continuation of a group name is the mistake being caught.
        if lowered in settings_cls.model_fields:
            return False
        if not any(
            lowered.startswith(f"{group}_") and not lowered.startswith(f"{group}__")
            for group in group_names
        ):
            return False
        # Only ambiguous if the correctly-spelled name is absent. A machine
        # with a stray `OPENAI_API_KEY` exported for some other tool is
        # common, and refusing to boot the whole IAM API over it would be a
        # worse failure than the one being prevented -- but if the correct
        # name is *also* set, that one wins and there is nothing to warn about.
        corrected = key.replace("_", "__", 1)
        return corrected not in candidates

    near_misses = sorted(key for key in candidates if _is_near_miss(key))
    if near_misses:
        corrected = ", ".join(
            f"{key} (did you mean {key.replace('_', '__', 1)}?)" for key in near_misses
        )
        raise ValueError(
            "refusing to start: these settings names use a single underscore where "
            f"the nested-group delimiter '__' is required, so they are read as nothing: {corrected}"
        )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
    )

    environment: Literal["development", "staging", "production"] = "development"
    secret_provider: Literal[
        "env", "aws_secrets_manager", "vault", "azure_key_vault", "gcp_secret_manager"
    ] = "env"
    #: Only read when secret_provider="aws_secrets_manager".
    aws_region: str = "us-east-1"

    database: DatabaseSettings
    redis: RedisSettings = RedisSettings()
    jwt: JwtSettings
    oauth_google: OAuthProviderSettings = OAuthProviderSettings()
    oauth_facebook: OAuthProviderSettings = OAuthProviderSettings()
    password_policy: PasswordPolicySettings = PasswordPolicySettings()
    lockout: LockoutSettings = LockoutSettings()
    mfa: MfaSettings = MfaSettings()
    rate_limit: RateLimitSettings = RateLimitSettings()
    encryption: EncryptionSettings

    # Knowledge-base ingestion and RAG (Phases 10-14). All default to empty
    # credentials so the IAM platform continues to boot and serve without
    # them -- the RAG features are additive, not load-bearing for
    # authentication. The adapters themselves refuse to operate with an empty
    # key rather than failing obscurely deep in an HTTP call.
    storage: StorageSettings = StorageSettings()
    qdrant: QdrantSettings = QdrantSettings()
    openai: OpenAISettings = OpenAISettings()
    cohere: CohereSettings = CohereSettings()
    tavily: TavilySettings = TavilySettings()
    ingestion: IngestionSettings = IngestionSettings()
    crawl: CrawlSettings = CrawlSettings()

    cors_allowed_origins: list[str] = []

    #: Where third-party pages reach this API, e.g. `https://api.example.com`.
    #:
    #: Needed only for the embeddable chat widget, and needed there because the
    #: admin console cannot supply it: the console deliberately ships no
    #: `NEXT_PUBLIC_*` backend origin (it talks to the API through a
    #: server-side proxy), so the browser genuinely does not know this value.
    #: A console that guessed would hand tenants an embed snippet pointing at
    #: its own authenticated proxy -- which works on no third-party site at all.
    #:
    #: Left empty, the API falls back to the request's own base URL, which is
    #: right in development and right in production only when the deployment
    #: passes through a truthful Host. Set it explicitly behind a proxy.
    public_api_base_url: str = ""

    log_level: str = "INFO"

    def __init__(self, **values: Any) -> None:
        # `_env_file` may override the class-level default (tests do this), so
        # the guard has to be told which file was actually read rather than
        # assuming `.env`.
        env_file = values.get("_env_file", type(self).model_config.get("env_file"))
        super().__init__(**values)
        _reject_near_miss_group_names(type(self), env_file)

    def model_post_init(self, __context: object) -> None:
        if self.environment == "production" and self.secret_provider == "env":
            raise ValueError(
                "refusing to start: environment=production requires a real secret "
                "provider (secret_provider=env means secrets are plain env vars)"
            )
