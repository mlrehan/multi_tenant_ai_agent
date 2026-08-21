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
from typing import Any, ClassVar, Literal, cast
from urllib.parse import quote

from dotenv import dotenv_values
from pydantic import BaseModel, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


def _nested_group_field_names(settings_cls: type[BaseSettings]) -> frozenset[str]:
    """Lowercase names of the model's nested-group fields (`jwt`, `openai`, ...).

    The one thing both `_reject_near_miss_group_names` and
    `_DropUnvalidatableKeys` need to agree on, so it is computed in exactly one
    place. The `RATE_LIMIT`/`OAUTH_GOOGLE` bug earlier in this file's history
    was two copies of "which group does this prefix belong to" drifting apart;
    this is what stops that happening a second time between the guard and the
    source filter.
    """
    return frozenset(
        name
        for name, field in settings_cls.model_fields.items()
        if isinstance(field.annotation, type) and issubclass(field.annotation, BaseModel)
    )


class _DropUnvalidatableKeys:
    """Wraps the dotenv source, removing keys that would never validate.

    Two categories, both explained at their call site in
    `settings_customise_sources`:

    1. A fixed, named allowlist (`Settings._COMPOSE_ONLY_KEYS`) -- names that
       are not a field under any spelling, ever.
    2. A flat spelling of a nested group (`jwt_issuer` rather than `jwt`).
       **Safe to drop unconditionally here, not merely likely-safe:**
       `_reject_near_miss_group_names` runs earlier in `Settings.__init__`,
       before `super().__init__()` is ever reached, and raises for exactly
       this shape of key *unless* the correctly-spelled group is also present.
       By the time this wrapper runs, execution has already stopped for every
       case where dropping the flat key would lose the only copy of a value --
       so recognising the shape is enough; there is no "is the correct one
       really there" check left to repeat.

    The dotenv source is what actually enumerates every line in `.env` as a
    candidate input -- `os.environ` is not scanned this way, which is why an
    unrelated `PATH` or `HOME` never trips `extra="forbid"` but a line sitting
    unused in the dotenv file does. Wrapping the source, rather than editing
    the dict some other way, keeps this a source in pydantic-settings' own
    pipeline -- it still participates in precedence ordering normally, it is
    just missing a few keys by the time anything downstream sees it.
    """

    def __init__(
        self,
        wrapped: PydanticBaseSettingsSource,
        compose_only: frozenset[str],
        group_names: frozenset[str],
    ) -> None:
        self._wrapped = wrapped
        self._compose_only = compose_only
        self._group_names = group_names

    def _is_flat_group_spelling(self, key: str) -> bool:
        # `key` arrives already lowercased -- the source matches it against
        # field names that way, not against the env var's actual casing.
        return any(
            key.startswith(f"{group}_") and not key.startswith(f"{group}__")
            for group in self._group_names
        )

    def __call__(self) -> dict[str, Any]:
        raw = self._wrapped()
        return {
            k: v
            for k, v in raw.items()
            # Comparing `.upper()` against the allowlist because that list is
            # written the way the env var actually looks in `.env`
            # (`WORKER_CONCURRENCY`), while `k` here is already lowercased.
            if k.upper() not in self._compose_only and not self._is_flat_group_spelling(k)
        }


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
    #
    # **Optional, and that is the point.** Only Alembic and the one-off ops
    # scripts ever build `migrator_dsn`; the API and the worker must never
    # hold DDL credentials, so a correctly-deployed one does not set this at
    # all. Requiring it here meant the opposite of what it looks like: those
    # services could not start *unless* handed the very credential they are
    # supposed to be denied. `migrator_dsn` fails loudly at use instead, which
    # is the only place the absence actually matters.
    migrator_user: str = "postgres"
    migrator_password: SecretStr | None = None

    pool_size: int = 10
    pool_max_overflow: int = 20

    def _dsn(self, user: str, password: SecretStr) -> str:
        """Assemble a connection URL, percent-encoding the credentials.

        **The encoding is not defensive padding -- without it a perfectly
        ordinary password silently authenticates as something else.** A URL
        gives `#`, `%`, `@`, `/` and `:` structural meaning: `#` starts a
        fragment, so `pa#ssword` is parsed as the password `pa`; `%41` is
        decoded to `A`. The connection then fails with
        `InvalidPasswordError: password authentication failed`, which reads as
        a wrong password rather than a mangled one, and the value in `.env` is
        correct so nobody suspects it.

        This is not a corner case: DEPLOYMENT.md tells operators to generate
        passwords with `openssl rand -base64 32`, whose alphabet includes `/`
        and `+`. Roughly half of all generated passwords contain at least one
        character that breaks an unencoded URL.

        SQLAlchemy percent-decodes these fields when it parses the URL, so
        encoding here round-trips exactly.
        """
        return (
            f"postgresql+asyncpg://{quote(user, safe='')}"
            f":{quote(password.get_secret_value(), safe='')}"
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
        if self.migrator_password is None:
            raise ValueError(
                "DATABASE__MIGRATOR_PASSWORD is not set. It is needed only by "
                "migrations and the ops scripts -- run those in the migration "
                "job, which has it, rather than in the API or worker container, "
                "which deliberately do not."
            )
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

    #: How hard a *reasoning* model should think before it answers. Sent only
    #: when set, for the same reason as `chat_temperature`: a non-reasoning
    #: model rejects the parameter outright, so a non-None default would break
    #: every answer on those models.
    #:
    #: **This is the biggest latency control on the answer path, and what it
    #: buys is predictability more than raw speed.** Measured against
    #: `gpt-5.5` with the real system prompt and a real retrieved passage,
    #: six questions each, time-to-first-token:
    #:
    #:     unset (model default)   min 1.03s   median 2.11s   max 10.80s
    #:     "low"                   min 0.83s   median 1.24s   max  1.58s
    #:
    #: The median barely moves; the *tail* collapses. Left unset the model
    #: decides how long to think and occasionally spends eleven seconds on a
    #: question the retriever had already answered -- and a reasoning model
    #: emits nothing at all while thinking, so that is a visitor watching an
    #: empty bubble with no evidence anything is happening. One such wait is
    #: what makes someone close the tab, which is why the worst case matters
    #: more here than the average.
    #:
    #: It costs nothing in answer quality because grounded answering is
    #: extraction and synthesis over passages the retriever already chose --
    #: not the kind of work deep reasoning improves.
    #:
    #: Valid values are model-dependent and *not* validated here: `gpt-5.5`
    #: accepts "low" and rejects "minimal". An unsupported value fails loudly
    #: at answer time rather than being silently dropped, which is the right
    #: trade -- a swallowed setting would leave the latency unexplained.
    chat_reasoning_effort: str | None = None

    request_timeout_seconds: float = 60.0


class CohereSettings(BaseModel):
    """Reranking of retrieved candidates (step 3 of the query pipeline in
    ``Architectural_Diagram.txt``)."""

    api_key: SecretStr = SecretStr("")
    rerank_model: str = "rerank-v3.5"


class TavilySettings(BaseModel):
    api_key: SecretStr = SecretStr("")


class PushSettings(BaseModel):
    """Web Push (VAPID) for notifying agents when the console is not open.

    **Unconfigured means the feature is absent, not broken.** `is_configured`
    is false without a keypair, `GET .../push/public-key` then reports that,
    and the console does not offer to subscribe. That is deliberate: the
    alternative shapes are both worse -- refusing to start would take the whole
    API down over an optional notification channel, and offering a Subscribe
    button that always fails would look like a bug in the browser.

    **Both keys are base64url strings, not PEM.** `pywebpush` hands a string
    private key to `py_vapid.Vapid.from_string`, which strips newlines and
    base64url-decodes it -- so a PKCS8 PEM pasted into an env var fails with
    "ASN.1 parsing error: invalid length" at the moment of the first send, not
    at startup. Found by sending a real push, not by reading the library.

    Generate a matching pair with:

        python -m scripts.generate_vapid_keys

    The **private key never leaves the server**; the public key is handed to
    every browser by design (it is what the push service uses to verify the
    signature), so it is not a secret and is not a `SecretStr`.
    """

    vapid_public_key: str = ""
    vapid_private_key: SecretStr = SecretStr("")

    #: The `sub` claim of the VAPID JWT. Push services require a contact --
    #: it is how they reach an operator whose sending is misbehaving rather
    #: than silently blocking them.
    vapid_subject: str = "mailto:ops@example.com"

    #: How long a push may sit queued if the agent's browser is offline. Ten
    #: minutes: a waiting visitor is a *now* problem, and a notification that
    #: arrives an hour later sends an agent to a conversation someone else has
    #: long since picked up.
    ttl_seconds: int = 600

    @property
    def is_configured(self) -> bool:
        return bool(self.vapid_public_key and self.vapid_private_key.get_secret_value())


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

    #: Read a PDF's embedded text layer before reaching for docling's ML
    #: layout models. Most business PDFs are text-native, where the layer is
    #: the author's own words and reading it is both far faster and strictly
    #: more accurate than inferring them back from a rendered image. Turn this
    #: off only to compare the two paths on a corpus.
    pdf_text_layer_first: bool = True
    #: A page with fewer characters than this counts as having no text --
    #: enough to ignore a stray page number or scanner watermark, low enough
    #: that a sparse title page still counts.
    pdf_min_chars_per_page: int = 50
    #: Fraction of pages that must carry text before the fast path is trusted.
    #: Below it the document is treated as scanned and handed to docling's OCR.
    #: Not 1.0: real documents contain full-page diagrams and blank separators,
    #: and one of those should not force a 100-page report through OCR.
    pdf_min_text_page_ratio: float = 0.6

    #: How many pages docling holds in memory at once, per stage.
    #:
    #: **This is a memory ceiling, not a throughput knob.** Docling's defaults
    #: batch 4 pages through layout, OCR and table detection simultaneously,
    #: and its OCR path renders each page at 3x scale -- roughly 8.7 megapixels
    #: for A4, several times that again as float tensors. Four of those at once
    #: across four threads is enough to exhaust the heap on a CPU worker: a
    #: 40-page scanned PDF failed with `std::bad_alloc` on pages 4 through 13,
    #: which is exactly the batch boundary.
    #:
    #: One page at a time is slower and *finishes*. Deliberately bounded here
    #: rather than by lowering the OCR scale: scale is what makes small print
    #: legible, so reducing it would trade a crash for silently worse text.
    docling_batch_size: int = 1
    #: Worker threads inside docling. Multiplies the above -- each thread can
    #: hold its own page tensors.
    docling_num_threads: int = 2
    #: Wall-clock ceiling for one document, in seconds. `None` means unbounded,
    #: which is docling's default and lets a pathological file occupy a worker
    #: indefinitely. 20 minutes is generous for a large scan and still bounded.
    docling_timeout_seconds: float | None = 1200.0
    #: Refuse a PDF longer than this rather than starting work that will not
    #: finish. Scanned pages cost roughly a second each even when nothing goes
    #: wrong, so a 2,000-page scan is not a document, it is an outage.
    docling_max_pages: int = 500

    #: OCR language(s) for RapidOCR. A single code; docling takes a list, and
    #: the adapter wraps it. Kept configurable because OCR accuracy is
    #: language-dependent and a deployment serving one region should say so.
    ocr_language: str = "english"
    #: Render scale for OCR. Higher reads smaller print and costs memory
    #: quadratically -- 3x on A4 is roughly 8.7 megapixels per page. Lower this
    #: only after the low-memory retry has proved insufficient, because the
    #: failure it trades into (quietly worse text) is harder to notice than
    #: the one it avoids.
    ocr_scale: float = 3.0

    #: Below this many characters, a natively-parsed Word or PowerPoint file
    #: is treated as possibly a scan in a wrapper and re-read with OCR --
    #: but only when the file also contains images. A short memo with no
    #: pictures is simply short and is left alone.
    native_office_min_chars: int = 200

    #: Bounds on what a ZIP-based document (DOCX/PPTX/XLSX/EPUB/ODF) may
    #: expand to. A ZIP is an attacker-controlled decompression instruction,
    #: and the worker holding database credentials is not the place to find
    #: out how far it expands. Generous for real documents: a 200-slide deck
    #: full of photographs sits far inside both.
    archive_max_entries: int = 5_000
    archive_max_uncompressed_bytes: int = 512 * 1024 * 1024


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

    def _correction(key: str) -> str | None:
        """The correctly-spelled name, or ``None`` if this key is not a near miss.

        **Matched against the longest group name, not the first underscore.**
        Several groups have underscores of their own -- `rate_limit`,
        `oauth_google`, `oauth_facebook`, `password_policy` -- and splitting on
        the first underscore turns `RATE_LIMIT_WINDOW_SECONDS` into
        `RATE__LIMIT_WINDOW_SECONDS`, which is not the right name. That broke
        the guard twice over: it advised a spelling that does not work, and the
        tolerance below then looked for that same wrong spelling, so setting
        the *correct* name alongside it never silenced the refusal.

        Longest-first because `oauth_google` and `oauth_facebook` would
        otherwise be shadowed by any shorter group that happens to prefix them.
        """
        lowered = key.lower()
        # A key that *is* a field name is fine; only a single-underscore
        # continuation of a group name is the mistake being caught.
        if lowered in settings_cls.model_fields:
            return None
        for group in sorted(group_names, key=len, reverse=True):
            if lowered.startswith(f"{group}_") and not lowered.startswith(f"{group}__"):
                return f"{group}__{lowered[len(group) + 1 :]}".upper()
        return None

    def _is_near_miss(key: str) -> bool:
        corrected = _correction(key)
        if corrected is None:
            return False
        # Only ambiguous if the correctly-spelled name is absent. A machine
        # with a stray `OPENAI_API_KEY` exported for some other tool is
        # common, and refusing to boot the whole IAM API over it would be a
        # worse failure than the one being prevented -- but if the correct
        # name is *also* set, that one wins and there is nothing to warn about.
        #
        # This also lets one `.env` serve both Docker Compose (which reads flat
        # `${JWT_ISSUER}` names) and the application (which needs `JWT__ISSUER`),
        # which is otherwise an unresolvable conflict between two tools reading
        # the same file.
        return corrected not in candidates

    near_misses = sorted(key for key in candidates if _is_near_miss(key))
    if near_misses:
        corrected = ", ".join(
            f"{key} (did you mean {_correction(key)}?)" for key in near_misses
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
        # Kept "forbid" -- docs/21-configuration-and-secrets.md records this as
        # a confirmed decision, and `test_config_near_miss_names.py` asserts
        # the exact friction it accepts (a stray `OPENAI_API_KEY` refuses the
        # boot even with the correct name also set). Weakening it to "ignore"
        # was tried and reverted: it silenced that intentional refusal too,
        # which is a different bug -- see `_COMPOSE_ONLY_KEYS` below for the
        # actual fix.
        extra="forbid",
    )

    #: Names that appear in `.env` for `docker compose`'s own `${VAR}`
    #: substitution (`docker-compose.dev.yml` / `docker-compose.prod.yml`) and
    #: are not, and will never be, a field on this model -- `POSTGRES_
    #: SUPERUSER_PASSWORD` seeds the `postgres` role compose creates,
    #: `APP_TENANT_PASSWORD`/`APP_PLATFORM_PASSWORD` seed the two application
    #: roles `docker/postgres-init/01-roles.sh` creates, `WORKER_CONCURRENCY`
    #: is a `celery` CLI flag. None of them is a misspelling of anything;
    #: `extra="forbid"` has no way to tell that apart from a genuine typo, so
    #: they are removed from the dotenv source explicitly, by name, before
    #: validation ever sees them -- see `settings_customise_sources` below.
    #:
    #: A fixed, reviewed list rather than a pattern match, deliberately: a
    #: pattern broad enough to catch "yet another compose-only name" would
    #: also swallow a real typo of a real field.
    #: `ClassVar`, not a plain class attribute: inside a pydantic model, a
    #: bare single-underscore name is intercepted as a *private model
    #: attribute* (`ModelPrivateAttr`) rather than left as a normal class
    #: constant, so `cls._COMPOSE_ONLY_KEYS` inside `settings_customise_sources`
    #: returned that descriptor instead of the frozenset -- caught immediately
    #: by every test that constructs `Settings()`, not silently.
    _COMPOSE_ONLY_KEYS: ClassVar[frozenset[str]] = frozenset(
        {
            "POSTGRES_SUPERUSER_PASSWORD",
            "APP_TENANT_PASSWORD",
            "APP_PLATFORM_PASSWORD",
            "WORKER_CONCURRENCY",
        }
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # `_DropUnvalidatableKeys` satisfies the source protocol structurally
        # (a zero-arg callable returning a dict) rather than by inheritance --
        # inheriting `PydanticBaseSettingsSource` pulls in constructor
        # machinery designed for reading *this* settings class from scratch,
        # not for wrapping an already-built source. `cast` here asserts that
        # structural match to mypy; it is not silencing a real type mismatch.
        return cast(
            "tuple[PydanticBaseSettingsSource, ...]",
            (
                init_settings,
                env_settings,
                _DropUnvalidatableKeys(
                    dotenv_settings,
                    cls._COMPOSE_ONLY_KEYS,
                    _nested_group_field_names(settings_cls),
                ),
                file_secret_settings,
            ),
        )

    environment: Literal["development", "staging", "production"] = "development"
    secret_provider: Literal[
        "env", "aws_secrets_manager", "vault", "azure_key_vault", "gcp_secret_manager"
    ] = "env"
    #: Only read when secret_provider="aws_secrets_manager".
    aws_region: str = "us-east-1"

    #: Deliberate opt-out from the "production needs a real secret store" rule
    #: below, for a single-server deployment that keeps its secrets in a
    #: `chmod 600` `.env` instead of a managed secret manager.
    #:
    #: A flag rather than simply dropping the rule, because the rule is worth
    #: keeping: its job is to stop a deployment reaching production with
    #: plaintext secrets *by accident* -- someone who copied a dev `.env` and
    #: changed `ENVIRONMENT`. Setting this is not something that happens by
    #: accident, so the rail still catches the case it was built for while an
    #: operator who has read what it means can proceed.
    #:
    #: The trade-off it accepts: anyone who can read the file, or a backup or
    #: image containing it, holds the JWT signing key and the encryption data
    #: key. On a host where the same `.env` already carries the database
    #: passwords, that is a difference of degree rather than kind -- but on a
    #: multi-tenant deployment holding other people's data it is worth
    #: revisiting.
    allow_plaintext_secrets: bool = False

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

    #: Agent push notifications. Optional in the same way and for the same
    #: reason: an inbox that only chimes in an open tab is a lesser product,
    #: not a broken one.
    push: PushSettings = PushSettings()

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
        # **Before** `super().__init__`, not after. Newer pydantic-settings
        # rejects a near miss itself with a generic `extra_forbidden` naming
        # the lowercased field (`openai_api_key`), which meant this guard never
        # ran and its whole contribution -- telling the operator the *correct*
        # spelling, `OPENAI__API_KEY` -- was silently lost. The deployment
        # still refused to start either way, so the regression was invisible
        # except as a worse error message.
        _reject_near_miss_group_names(type(self), env_file)
        super().__init__(**values)

    def model_post_init(self, __context: object) -> None:
        if (
            self.environment == "production"
            and self.secret_provider == "env"
            and not self.allow_plaintext_secrets
        ):
            raise ValueError(
                "refusing to start: environment=production requires a real secret "
                "provider (secret_provider=env means secrets are plain env vars). "
                "Set ALLOW_PLAINTEXT_SECRETS=true to accept that deliberately -- "
                "see DEPLOYMENT.md."
            )
