"""AI-resource ports, including ``AiResourceUnitOfWork`` -- the RLS-subject
(``app_tenant``) transaction boundary for every AI-resource operation.

Reuses ``TenantMembershipRepository`` from ``application.tenancy.ports``
rather than redefining it: resolving the requester's department/team for the
visibility policy is a membership lookup, and duplicating that port would
mean two Protocols the same SQL repository has to satisfy.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import TracebackType
from typing import Any, Protocol
from uuid import UUID

from iam_platform.application.identity.ports import AuditWriter, SecurityEventWriter
from iam_platform.application.tenancy.ports import TenantMembershipRepository
from iam_platform.domain.ai_resources.entities import (
    AiAssistant,
    AssistantMember,
    ChatWidget,
    Conversation,
    DataSource,
    Document,
    KnowledgeBase,
    ModelConfiguration,
    ProviderCredential,
)


class AiAssistantRepository(Protocol):
    async def get_by_id(self, assistant_id: UUID) -> AiAssistant | None: ...
    async def list_by_tenant(self, tenant_id: UUID) -> list[AiAssistant]:
        """Every non-archived assistant in the tenant -- RLS guarantees the
        tenant scoping; per-resource *visibility* filtering happens in the use
        case via ``domain.ai_resources.policies``, not here, so the policy
        stays a pure testable function instead of leaking into SQL."""
        ...

    async def add(self, assistant: AiAssistant) -> None: ...
    async def save(self, assistant: AiAssistant) -> None: ...


class AssistantMemberRepository(Protocol):
    async def get(self, *, assistant_id: UUID, membership_id: UUID) -> AssistantMember | None: ...
    async def list_by_assistant(self, assistant_id: UUID) -> list[AssistantMember]: ...
    async def list_for_membership(self, membership_id: UUID) -> list[AssistantMember]:
        """All explicit grants held by one member -- loaded once per request so
        a list-assistants call doesn't issue a lookup per candidate row."""
        ...

    async def add(self, member: AssistantMember) -> None: ...
    async def remove(self, *, assistant_id: UUID, membership_id: UUID) -> None: ...


class KnowledgeBaseRepository(Protocol):
    async def get_by_id(self, knowledge_base_id: UUID) -> KnowledgeBase | None: ...
    async def list_by_tenant(self, tenant_id: UUID) -> list[KnowledgeBase]: ...
    async def add(self, knowledge_base: KnowledgeBase) -> None: ...
    async def save(self, knowledge_base: KnowledgeBase) -> None: ...


class DocumentRepository(Protocol):
    async def get_by_id(self, document_id: UUID) -> Document | None: ...
    async def list_by_knowledge_base(self, knowledge_base_id: UUID) -> list[Document]:
        """Excludes soft-deleted rows."""
        ...

    async def add(self, document: Document) -> None: ...
    async def save(self, document: Document) -> None: ...

    async def delete_chunks(self, document_id: UUID) -> int:
        """Removes a document's chunk rows. Returns how many were deleted.

        On the document repository rather than a `DocumentChunkRepository` of
        its own: chunks have no identity apart from the document they were cut
        from, are never listed or fetched individually by the application, and
        exist so the vector index can be rebuilt without re-parsing. A separate
        repository would be a second way to reach the same rows.
        """
        ...

    async def count_chunks(self, document_id: UUID) -> int:
        """How many chunks a document currently has indexed.

        The honest measure of whether a document is usable: `status = 'ready'`
        says the pipeline finished, this says it produced something to search.
        """
        ...


class DataSourceRepository(Protocol):
    async def add(self, source: DataSource) -> None: ...
    async def get(self, *, tenant_id: UUID, source_id: UUID) -> DataSource | None: ...
    async def list_for_knowledge_base(
        self, *, tenant_id: UUID, knowledge_base_id: UUID
    ) -> list[DataSource]: ...


class ChatWidgetRepository(Protocol):
    async def add(self, widget: ChatWidget) -> None: ...
    async def list_for_tenant(self, tenant_id: UUID) -> list[ChatWidget]: ...

    async def get_for_tenant(self, tenant_id: UUID, widget_id: UUID) -> ChatWidget | None:
        """Tenant-scoped by argument as well as by RLS.

        Both, not either: RLS is the backstop that holds when application code
        forgets, and an explicit predicate is what makes the intent readable at
        the call site. Neither is redundant with the other.
        """
        ...

    async def update(self, widget: ChatWidget) -> None: ...


class PublicWidgetLookup(Protocol):
    """Resolves a public key to a widget, *before* any tenant is known.

    Deliberately not a method on `ChatWidgetRepository`: that one runs under
    RLS with a tenant context set, and this lookup cannot -- the whole point is
    that the caller has supplied only a public key and the tenant is what we
    are trying to discover. It is the one read in this platform that
    legitimately crosses the tenant boundary, so it is a separate, narrow port
    with exactly one method rather than a flag on a general-purpose repository
    where it could be reached by accident.
    """

    async def find_by_public_key(self, public_key: str) -> ChatWidget | None: ...

    async def find_by_widget_id(self, widget_id: UUID) -> ChatWidget | None:
        """Re-reads a widget mid-session, by the id carried in a token.

        Also tenant-crossing by necessity and for the same reason: the caller
        is a visitor with no tenant context. Narrow: one row, by primary key.
        """
        ...


class WidgetQuotaStore(Protocol):
    """Counts questions per widget per day.

    Redis-backed in production, and like every other use of Redis here it
    **fails closed**: if the count cannot be confirmed, the question is
    refused. An unavailable counter must not become unlimited spending on the
    platform's bill (docs/06-authorization-model.md).
    """

    async def consume(self, *, widget_id: UUID, limit: int) -> bool:
        """Records one question and returns whether it was within the limit."""
        ...


class ConversationRepository(Protocol):
    async def get_by_id(self, conversation_id: UUID) -> Conversation | None: ...
    async def list_by_membership(self, membership_id: UUID) -> list[Conversation]: ...
    async def add(self, conversation: Conversation) -> None: ...
    async def save(self, conversation: Conversation) -> None: ...


class ModelConfigurationRepository(Protocol):
    async def get_by_id(self, model_configuration_id: UUID) -> ModelConfiguration | None: ...
    async def list_available_to_tenant(self, tenant_id: UUID) -> list[ModelConfiguration]:
        """Platform defaults (``tenant_id IS NULL``) plus this tenant's own."""
        ...

    async def add(self, model_configuration: ModelConfiguration) -> None: ...


class ProviderCredentialRepository(Protocol):
    async def get_by_id(self, credential_id: UUID) -> ProviderCredential | None: ...
    async def list_by_tenant(self, tenant_id: UUID) -> list[ProviderCredential]: ...
    async def add(self, credential: ProviderCredential) -> None: ...
    async def save(self, credential: ProviderCredential) -> None: ...


# --- Supporting services -----------------------------------------------------


class CredentialEncryptor(Protocol):
    """Envelope encryption for provider secrets (docs/16-schema-ai-resources.md).

    ``decrypt`` exists on the port but is deliberately not called by any use
    case in this phase -- only the AI-execution infrastructure service
    decrypts, at model-call time. Keeping it here documents the boundary
    rather than pretending the capability doesn't exist.
    """

    def encrypt(self, plaintext: str) -> bytes: ...
    def decrypt(self, ciphertext: bytes) -> str: ...
    def key_hint(self, plaintext: str) -> str:
        """Last few characters only -- the sole part of a secret any UI sees."""
        ...


class VectorNamespaceFactory(Protocol):
    """Generates the server-side vector namespace for a knowledge base.

    A port rather than a bare function so the "never client-suppliable" rule
    is structurally enforced: there is no code path that accepts a namespace,
    only one that derives it.
    """

    def build(self, *, tenant_id: UUID, knowledge_base_id: UUID) -> str: ...


class ObjectStoragePathFactory(Protocol):
    def build(self, *, tenant_id: UUID, knowledge_base_id: UUID, document_id: UUID) -> str: ...


class ObjectStorageClient(Protocol):
    """Reads and writes the actual document bytes.

    Deliberately separate from ``ObjectStoragePathFactory``: the factory
    decides *where* something goes (a security property -- the path is
    server-derived and tenant-scoped, never client-supplied), this moves bytes
    to and from that location. Splitting them keeps the security-relevant half
    a pure function with no I/O to mock when testing it.

    Every method takes a ``path`` that a caller obtained from the factory.
    There is no "list everything" or "read by prefix" method, and that's
    intentional: such an operation would be the natural place for a
    cross-tenant read to hide, and nothing in the ingestion pipeline needs one.
    """

    async def put(self, *, path: str, data: bytes, content_type: str) -> None: ...

    async def get(self, *, path: str) -> bytes:
        """Raises ``DocumentContentNotFoundError`` if the object is absent --
        an internal inconsistency (the row exists, the bytes don't), not a
        client-visible 404. See that exception's docstring."""
        ...

    async def delete(self, *, path: str) -> None:
        """Idempotent: deleting an absent object is not an error. A purge job
        that crashes half-way must be safe to re-run."""
        ...


@dataclass(frozen=True, slots=True)
class ParsedBlock:
    """A contiguous run of text with a human-meaningful origin.

    Parsers emit blocks, not one flat string, so ``source_location`` survives
    into each chunk and a citation can eventually say "page 7" or
    "Sheet1 row 42" rather than pointing at the document as an undifferentiated
    whole. Chunking may split a block or merge several, but never merges across
    two different ``source_location`` values.
    """

    text: str
    source_location: str | None


@dataclass(frozen=True, slots=True)
class TextChunk:
    """A chunk ready to be embedded -- text plus where it came from.

    Distinct from ``VectorChunk``, which additionally carries the embedding and
    the ids needed to store it. Keeping them separate means the chunker has no
    reason to know about documents or tenants, and stays a pure function of
    text in, text out.
    """

    text: str
    token_count: int
    source_location: str | None


class DocumentParser(Protocol):
    """Extracts text from raw document bytes.

    Takes ``content_type`` and ``filename`` rather than sniffing, because the
    upload endpoint has already validated both against an allow-list -- a
    parser that re-guessed could disagree with what was authorized.
    """

    def supports(self, *, content_type: str, filename: str) -> bool: ...

    async def parse(
        self, *, data: bytes, content_type: str, filename: str
    ) -> list[ParsedBlock]:
        """Raises ``DocumentParseError`` when the bytes cannot be read at all
        (corrupt, encrypted, or not the declared format)."""
        ...


class EmbeddingClient(Protocol):
    """Turns text into vectors, for both indexing and querying.

    Two methods rather than one because the two call sites have genuinely
    different shapes, not because the model treats them differently: ingestion
    embeds many chunks at once and benefits from batching, while a query
    embeds exactly one string on a latency-sensitive path. A single
    list-taking method would push per-call list wrapping onto the query path
    and hide the batching concern from the ingestion one.

    ``dimensions`` is exposed because the vector store needs it to size a
    collection, and the only authority on it is whatever the client was
    configured to request -- deriving it from a hardcoded model→size table
    would let the two drift apart the moment the model changes.
    """

    @property
    def dimensions(self) -> int: ...

    async def embed(self, text: str) -> list[float]: ...

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Returns one vector per input, **in input order**. Callers zip the
        result against their chunks, so a reordered response would silently
        attach every embedding to the wrong text."""
        ...


@dataclass(frozen=True, slots=True)
class VectorChunk:
    """One embedded passage, ready to be written to the vector store.

    ``knowledge_base_id`` is carried explicitly rather than left implicit in
    the namespace because it becomes the in-collection filter: vectors for
    every knowledge base in a tenant share one collection, so the field has to
    exist on the record itself to be filterable.
    """

    chunk_id: UUID
    document_id: UUID
    knowledge_base_id: UUID
    text: str
    embedding: list[float]
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    """One passage returned by search, with enough provenance to cite it.

    Distinct from `VectorChunk` (which carries an embedding on the way *in*)
    because nothing downstream of retrieval needs the vector, and passing it
    around would mean 3072 floats per chunk through the whole pipeline for no
    reader.
    """

    chunk_id: UUID
    document_id: UUID
    text: str
    score: float
    #: Where in the source document this came from -- a page, a row, a URL.
    #: Carried so a citation can point somewhere a person can actually look.
    source_location: str | None = None


class VectorSearchClient(Protocol):
    async def ensure_namespace(self, *, namespace: str, dimensions: int) -> None:
        """Idempotently provisions storage for ``namespace``.

        Called before the first write for a knowledge base. Takes
        ``dimensions`` from the embedding client rather than configuration, so
        the index can only ever be sized to match the vectors actually being
        produced.
        """
        ...

    async def upsert(self, *, namespace: str, chunks: list[VectorChunk]) -> None:
        """Writes or replaces chunks. Keyed by ``chunk_id``, so re-ingesting a
        document that produced the same chunk ids overwrites rather than
        duplicating."""
        ...

    async def delete_document(self, *, namespace: str, document_id: UUID) -> None:
        """Removes every chunk belonging to one document. Idempotent -- a
        purge that crashed part-way must be safe to re-run."""
        ...

    async def query(
        self, *, namespace: str, query_text: str, top_k: int
    ) -> list[tuple[UUID, float]]:
        """Returns ``(document_id, score)`` pairs. ``namespace`` is always
        supplied by the caller from the knowledge base's stored value -- the
        client has no way to search across namespaces."""
        ...

    async def search_chunks(
        self, *, namespace: str, query_text: str, top_k: int
    ) -> list[RetrievedChunk]:
        """Chunk-level results, for retrieval-augmented generation.

        Distinct from ``query`` rather than a flag on it: that method collapses
        chunks to their best-scoring *document*, which is right for "which
        files match?" and wrong for grounding an answer. A generator needs the
        passages themselves, several of which may come from one document -- the
        very thing ``query`` deliberately discards.

        Same namespace discipline: the caller supplies it from the stored
        knowledge-base row, so there is no way to search across tenants.
        """
        ...


class AiResourceUnitOfWork(Protocol):
    tenant_memberships: TenantMembershipRepository
    assistants: AiAssistantRepository
    assistant_members: AssistantMemberRepository
    knowledge_bases: KnowledgeBaseRepository
    documents: DocumentRepository
    data_sources: DataSourceRepository
    chat_widgets: ChatWidgetRepository
    conversations: ConversationRepository
    model_configurations: ModelConfigurationRepository
    provider_credentials: ProviderCredentialRepository
    audit: AuditWriter
    security_events: SecurityEventWriter

    async def __aenter__(self) -> AiResourceUnitOfWork: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...


# (user_id, tenant_id) -> a UoW bound to that RLS context. Unlike
# ``TenantUowFactory`` the tenant_id is non-optional: there is no
# "before a tenant is known" bootstrap step for AI resources -- every one of
# them is tenant-owned.
AiResourceUowFactory = Callable[[UUID, UUID], AiResourceUnitOfWork]


class DocumentIngestionQueue(Protocol):
    """Hands an uploaded document off for async chunking/embedding.

    ``actor_user_id`` is part of the payload because the worker re-validates
    the job's authorization against the database before doing anything
    (docs/18's worker-context rule, threat-model scenario 8) -- and it cannot
    re-check an actor it was never told about. Note what this does *not* mean:
    the id conveys no privilege on its own. It names whose authorization to
    re-derive at execution time, so a tampered payload buys nothing beyond
    being refused under a different name.
    """

    async def enqueue(
        self, *, tenant_id: UUID, actor_user_id: UUID, document_id: UUID, at: datetime
    ) -> None: ...


class CrawlMode(StrEnum):
    """What a crawl source means by its list of URLs."""

    #: Fetch exactly these URLs and nothing else. No link following.
    URL_LIST = "url_list"
    #: Treat the URL as a starting point and follow links within the same
    #: site, bounded by the configured depth and page budget.
    SITE = "site"


@dataclass(frozen=True, slots=True)
class CrawlLimits:
    """The bounds a crawl runs under.

    Passed explicitly rather than read from settings inside the adapter, so a
    per-source override is expressible later without changing the port, and so
    a test can drive a two-page crawl without rewriting global configuration.
    """

    max_depth: int
    max_pages: int
    page_timeout_seconds: int
    job_timeout_seconds: int
    respect_robots_txt: bool
    max_page_bytes: int


@dataclass(frozen=True, slots=True)
class CrawledPage:
    """One fetched page, already reduced to text.

    ``markdown`` rather than raw HTML: the crawler strips navigation, headers,
    footers and script tags, because indexing those would put a site's cookie
    banner into every chunk and make retrieval worse the larger the site gets.
    """

    url: str
    title: str | None
    markdown: str


class WebCrawler(Protocol):
    """Fetches pages for URL and website ingestion.

    Yields rather than returns a list: a 500-page crawl held entirely in
    memory before any of it is indexed would both delay all progress until the
    end and lose everything if the last page failed. Streaming means each page
    is embedded and committed as it arrives, so a crawl interrupted at page
    400 has indexed 400 pages rather than none.

    **Implementations must apply `assert_safe_to_fetch` to every URL they are
    about to request** -- the submitted ones, every redirect target, and every
    link discovered while crawling. Validating only at the boundary and
    trusting thereafter is what makes most SSRF filters decorative.
    """

    def crawl(
        self, *, urls: Sequence[str], mode: CrawlMode, limits: CrawlLimits
    ) -> AsyncIterator[CrawledPage]: ...


class UrlValidator(Protocol):
    """Refuses a URL this platform will not fetch.

    A port rather than a direct call to
    `infrastructure.crawling.url_safety.assert_safe_to_fetch`, because the
    application layer may not import from `infrastructure` (docs/20). The
    concrete guard is wired in at the composition root like every other
    adapter, and a test can drive the use case without DNS resolution.
    """

    def assert_safe(self, url: str) -> None: ...


class CrawlJobQueue(Protocol):
    """Hands a crawl source off for async fetching and indexing.

    Separate from `DocumentIngestionQueue` rather than an overload of it: the
    payloads name different things (a document versus a data source), and the
    two jobs have very different runtimes -- a document parse is seconds, a
    500-page crawl is up to two hours. Keeping them distinct is what lets them
    be routed to separate worker pools when volume justifies it, without one
    starving the other.

    Carries `actor_user_id` for the same reason the document queue does: the
    worker re-validates the job's authorization against the database before
    doing anything, and cannot re-check an actor it was never told about.
    """

    async def enqueue(
        self, *, tenant_id: UUID, actor_user_id: UUID, data_source_id: UUID, at: datetime
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class RerankedChunk:
    """A chunk with a relevance score from a cross-encoder, not a vector.

    Kept distinct from `RetrievedChunk` so the two scores can never be
    compared or averaged by accident: an embedding cosine and a reranker
    relevance are different quantities on different scales, and the whole
    point of reranking is that the second disagrees with the first.
    """

    chunk: RetrievedChunk
    relevance: float


class Reranker(Protocol):
    """Re-orders candidate passages against the query.

    Why this exists at all, given search already ranked them: embedding search
    compares a *summary* of the query to a *summary* of each passage, so it
    retrieves broadly and ranks crudely. A cross-encoder reads query and
    passage together and is far better at "does this actually answer it" --
    but is too slow to run over a whole corpus. Retrieve wide with the fast
    one, then narrow with the accurate one.
    """

    async def rerank(
        self, *, query: str, chunks: list[RetrievedChunk], top_n: int
    ) -> list[RerankedChunk]: ...


@dataclass(frozen=True, slots=True)
class GroundingContext:
    """One passage offered to the model, with the label it must cite by."""

    label: str
    text: str
    chunk: RetrievedChunk


class ChatModel(Protocol):
    """Streams a grounded answer.

    Streams rather than returns: an answer grounded in five passages takes
    seconds to generate, and a visitor watching a blank box for five seconds
    assumes it is broken. Token-by-token delivery is the difference between
    "slow" and "not working".
    """

    def stream_answer(
        self, *, question: str, context: list[GroundingContext], system_prompt: str
    ) -> AsyncIterator[str]: ...


class WidgetSessionIssuer(Protocol):
    """Mints and verifies visitor session tokens.

    A port rather than the concrete `WidgetTokenService`, so `api` does not
    import `infrastructure` (docs/20). The return types are intentionally
    loose here: the claims object is defined beside the implementation, and
    pinning its shape in this layer would drag a crypto concern into the
    application boundary for no benefit.
    """

    def issue(
        self,
        *,
        widget_id: UUID,
        tenant_id: UUID,
        knowledge_base_id: UUID,
        origin: str,
        now: datetime,
    ) -> Any: ...

    def verify(self, token: str) -> Any: ...
