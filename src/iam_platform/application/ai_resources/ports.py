"""AI-resource ports, including ``AiResourceUnitOfWork`` -- the RLS-subject
(``app_tenant``) transaction boundary for every AI-resource operation.

Reuses ``TenantMembershipRepository`` from ``application.tenancy.ports``
rather than redefining it: resolving the requester's department/team for the
visibility policy is a membership lookup, and duplicating that port would
mean two Protocols the same SQL repository has to satisfy.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, tzinfo
from enum import StrEnum
from types import TracebackType
from typing import Any, Protocol
from uuid import UUID

from iam_platform.application.identity.ports import AuditWriter, SecurityEventWriter
from iam_platform.application.tenancy.ports import TenantMembershipRepository
from iam_platform.domain.ai_resources.chatbot import TenantChatbotSettings
from iam_platform.domain.ai_resources.entities import (
    AiAssistant,
    AssistantMember,
    ChatWidget,
    Conversation,
    ConversationMessage,
    ConversationState,
    DataSource,
    Document,
    HandoffInitiator,
    KnowledgeBase,
    ModelConfiguration,
    ProviderCredential,
)
from iam_platform.domain.ai_resources.push import PushMessage, PushSubscription
from iam_platform.domain.tenancy.entitlements import TenantEntitlements
from iam_platform.domain.tenancy.teams import TenantTeam


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


@dataclass(frozen=True, slots=True)
class StoredChunk:
    """One persisted chunk, as stored rather than as retrieved.

    Distinct from `RetrievedChunk`, which carries a relevance `score` because
    it only exists as the answer to a query. This one has no score -- it is
    what is *in* the document, read by a person inspecting why a document
    answers (or fails to answer) questions, so `token_count` and the ordinal
    matter and relevance does not.
    """

    chunk_id: UUID
    chunk_index: int
    text: str
    token_count: int
    source_location: str | None = None


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
        from, are only ever reached through it, and exist so the vector index
        can be rebuilt without re-parsing. A separate repository would be a
        second way to reach the same rows.
        """
        ...

    async def list_chunks(
        self, document_id: UUID, *, limit: int, offset: int
    ) -> list[StoredChunk]:
        """A page of the document's chunks, in the order they were cut.

        Paged rather than returned whole: a large PDF runs to hundreds of
        chunks, and this is read by a human inspecting one document, not by
        the retrieval path.
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
    async def save(self, source: DataSource) -> None:
        """Persists a status transition (`mark_syncing`, `mark_failed`, ...).

        Only the mutable status fields are written. `urls`, `mode` and `kind`
        are what the source *is*; changing those would be a different crawl
        wearing the same row's history, so they are set once at creation.
        """
        ...

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

    async def count_conversations(self, *, tenant_id: UUID, widget_id: UUID) -> int:
        """How many conversations this widget has produced.

        Asked before a delete. The foreign key would refuse the delete
        anyway, but a count lets the refusal name a number and point at
        the alternative instead of surfacing as an IntegrityError.
        """
        ...

    async def delete(self, *, tenant_id: UUID, widget_id: UUID) -> None:
        """Hard delete. Only ever called for a widget with no
        conversations -- see `DeleteChatWidget` for why."""
        ...


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


class TypingIndicatorStore(Protocol):
    """Who is composing a message right now, on either end of a conversation.

    Deliberately *not* a repository: this is state with a lifetime measured in
    seconds and no record worth keeping. Modelling it as a port anyway keeps
    the routes free of an `infrastructure` import (docs/20) and leaves the
    door open to a transport that pushes rather than polls.
    """

    async def mark_typing(
        self, *, conversation_id: str, side: str, display_name: str = ""
    ) -> None:
        """Asserts, or renews, that this side is typing."""
        ...

    async def clear(self, *, conversation_id: str, side: str) -> None: ...

    async def who_is_typing(self, *, conversation_id: str, side: str) -> str | None:
        """Display name, or `None` when nobody is typing. Never raises."""
        ...


class WidgetMemoryStore(Protocol):
    """Recent turns for one anonymous widget session.

    Separate from `ConversationMessageRepository` on purpose: that one persists
    a member's owned history in Postgres under RLS, and a visitor has no
    identity to own anything. This is session-scoped, expires with the token,
    and **fails open** -- see the Redis adapter for why the asymmetry with the
    quota store beside it is deliberate.
    """

    async def recent(self, session_id: UUID) -> list[tuple[str, str]]:
        """`(role, content)` oldest first. Empty when unavailable."""
        ...

    async def append(self, session_id: UUID, *, question: str, answer: str) -> None: ...


class TokenUsageStore(Protocol):
    """Monthly token spend per model configuration, per tenant.

    **Read before, record after** -- deliberately not the widget quota's
    consume-then-check shape. A question's token cost is not known until the
    model has answered, so there is nothing to consume up front; the check is
    against what previous answers already spent. That means a single answer can
    overshoot the budget by its own size, which is accepted: the alternative is
    estimating the cost beforehand and either over-refusing on a bad guess or
    still being wrong. The budget bounds a month, not an individual answer.

    Like every other use of Redis here, the *check* *fails closed*: a budget
    that cannot be confirmed refuses the answer rather than allowing unbounded
    spending on someone's bill. Recording, by contrast, fails open and merely
    logs -- an answer the tenant has already been given and already been
    charged for by the provider must not also raise in their face, and the next
    check will read a slightly low number rather than none at all.
    """

    async def read(self, *, tenant_id: UUID, model_configuration_id: UUID) -> int:
        """Tokens spent this calendar month. 0 when nothing is recorded."""
        ...

    async def record(
        self, *, tenant_id: UUID, model_configuration_id: UUID, tokens: int
    ) -> None:
        """Adds to this month's total. Never raises."""
        ...


class ConversationRepository(Protocol):
    async def get_by_id(self, conversation_id: UUID) -> Conversation | None: ...
    async def list_by_membership(self, membership_id: UUID) -> list[Conversation]: ...
    async def list_by_tenant(self, tenant_id: UUID) -> list[Conversation]:
        """Every conversation in the tenant -- the admin oversight read."""
        ...

    async def list_by_ids(self, conversation_ids: list[UUID]) -> list[Conversation]: ...

    async def find_by_visitor_session(
        self, *, tenant_id: UUID, visitor_session_id: UUID
    ) -> Conversation | None:
        """The thread belonging to one widget session.

        Every widget exchange is persisted from the first question, so a
        session that has asked anything has a row here. `None` means a session
        that has not yet spoken -- or a brand-new one, which is what a visitor
        who has cleared their site data looks like.

        The session id is what makes a visitor's history durable: it is carried
        forward across token mints (see `WidgetTokenService.issue`), so a
        refresh or a return visit finds the same conversation instead of
        silently starting another.
        """
        ...
    async def add(self, conversation: Conversation) -> None: ...
    async def save(self, conversation: Conversation) -> None: ...
    async def delete(self, conversation_id: UUID) -> None:
        """Hard delete. Messages go with it via ON DELETE CASCADE -- a person
        asking for their conversation to be removed means the content, and a
        soft-deleted thread whose turns remain readable is not a deletion."""
        ...


class ConversationMessageRepository(Protocol):
    """Turns within a conversation. Append-only by design -- there is no
    `save`, and the table revokes UPDATE from the application role."""

    async def add_many(self, messages: list[ConversationMessage]) -> None: ...

    async def next_seq(self, conversation_id: UUID) -> int:
        """The ordinal the next message takes. 1 for an empty thread."""
        ...

    async def list_after(
        self, *, conversation_id: UUID, after_seq: int
    ) -> list[ConversationMessage]:
        """The uncompacted tail, in order.

        A range rather than the whole thread: everything at or before
        `after_seq` is already represented by the conversation's summary, and
        re-reading it each turn is the cost the summary exists to avoid.
        """
        ...

    async def list_page(
        self, *, conversation_id: UUID, limit: int, offset: int
    ) -> list[ConversationMessage]:
        """A page of the thread for *display*, oldest first."""
        ...

    async def list_tail(
        self, *, conversation_id: UUID, limit: int, before_seq: int | None = None
    ) -> list[ConversationMessage]:
        """The most **recent** turns, oldest first, paged backwards by cursor.

        Separate from `list_page` rather than a flag on it, because the two
        answer opposite questions: "the start of this thread" and "what is
        being said now". Paged by `seq` cursor and never by offset -- a live
        conversation gains rows between requests, and an offset slides under
        them, skipping or repeating turns.
        """
        ...

    async def count_for_conversation(self, conversation_id: UUID) -> int:
        """Total turns, so a caller can tell "there is more above" from "this
        is the beginning" without fetching a page to find out."""
        ...

    async def search(
        self, *, tenant_id: UUID, membership_id: UUID | None, text: str, limit: int
    ) -> list[UUID]:
        """Conversation ids whose messages match. `membership_id=None` searches
        the whole tenant, which only a tenant admin's use case may ask for."""
        ...


class ModelConfigurationRepository(Protocol):
    async def get_by_id(self, model_configuration_id: UUID) -> ModelConfiguration | None: ...
    async def list_available_to_tenant(self, tenant_id: UUID) -> list[ModelConfiguration]:
        """Configurations this tenant has been *granted*, excluding archived.

        Not "platform-owned plus my own" any more: availability is an explicit
        grant in `tenant_model_configurations`, so a platform-owned model is
        offered to a tenant only when someone decided it should be.
        """
        ...

    async def is_available_to_tenant(
        self, *, tenant_id: UUID, model_configuration_id: UUID
    ) -> bool:
        """Whether this tenant may assign this configuration.

        The application-layer half of the check whose other half is the
        `fk_ai_assistants_model_configuration` foreign key. Both exist on
        purpose: this one produces a clean 404 that reveals nothing, the
        constraint guarantees the rule holds even if this call is ever
        forgotten. Archived configurations are unavailable for *new*
        assignments while remaining valid for assistants already using them.
        """
        ...

    async def credential_for_tenant(
        self, *, tenant_id: UUID, model_configuration_id: UUID
    ) -> UUID | None:
        """This tenant's own provider credential for this model, if attached.

        On the *grant*, not the configuration: one configuration is granted to
        many tenants, so it cannot express whose key pays for whose questions.
        """
        ...

    async def credentials_for_tenant(self, tenant_id: UUID) -> dict[UUID, UUID]:
        """Every BYOK attachment this tenant has, keyed by configuration id.

        Configurations with no attachment are absent, not mapped to None.
        """
        ...

    async def set_credential_for_tenant(
        self,
        *,
        tenant_id: UUID,
        model_configuration_id: UUID,
        provider_credential_id: UUID | None,
    ) -> int:
        """Attach (or detach, with None) the tenant's key. Returns rows matched.

        Zero means no such grant -- reported as *not found*, never as a failed
        write, so a configuration the tenant was never granted stays
        unprovable.
        """
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

    async def embed(
        self, text: str, *, usage: TokenUsage | None = None
    ) -> list[float]:
        """Embeds one string, accumulating its token cost into `usage`.

        `usage` is optional and additive rather than returned, matching
        `ChatModel.stream_answer`: one answer spends tokens on an embedding
        *and* a completion, and the caller wants their sum, not two numbers to
        add up itself. Omitted, the call is exactly what it was before -- an
        unmetered path must not start behaving differently.
        """
        ...

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
        self,
        *,
        namespace: str,
        query_text: str,
        top_k: int,
        usage: TokenUsage | None = None,
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


class ConversationHandoffRepository(Protocol):
    """Handoff transitions written as conditional updates.

    Every method reports whether a row actually matched the state it expected.
    That is what lets two agents claiming the same conversation be told apart:
    the check and the write are one statement, so exactly one of them wins and
    the other is told so rather than silently overwriting.
    """

    async def route_to_team(
        self,
        *,
        tenant_id: UUID,
        conversation_id: UUID,
        team_id: UUID | None,
        reason: str | None,
        initiated_by: HandoffInitiator,
        now: datetime,
    ) -> bool: ...

    async def claim(
        self, *, tenant_id: UUID, conversation_id: UUID, membership_id: UUID, now: datetime
    ) -> bool:
        """False means another agent got there first."""
        ...

    async def set_state(
        self,
        *,
        tenant_id: UUID,
        conversation_id: UUID,
        state: ConversationState,
        now: datetime,
        clear_assignment: bool = False,
    ) -> bool: ...

    async def set_ai_fallback_disabled(
        self, *, tenant_id: UUID, conversation_id: UUID, disabled: bool, now: datetime
    ) -> bool:
        """False means no such conversation in this tenant."""
        ...

    async def purge_expired_conversations(
        self, *, tenant_id: UUID, older_than: datetime
    ) -> int:
        """Deletes conversations last active before `older_than`. Returns how
        many, so the sweep can report what it actually removed rather than
        that it ran."""
        ...

    async def list_unassigned(
        self, *, tenant_id: UUID, team_ids: list[UUID] | None = None
    ) -> list[Any]:
        """`team_ids=None` => every team (tenant-admin oversight). A list scopes
        an agent to the teams they actually staff."""
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
    conversation_messages: ConversationMessageRepository
    model_configurations: ModelConfigurationRepository
    provider_credentials: ProviderCredentialRepository
    entitlements: TenantEntitlementRepository
    chatbot_settings: TenantChatbotSettingsRepository
    teams: TenantTeamRepository
    handoff: ConversationHandoffRepository
    push_subscriptions: PushSubscriptionRepository
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


@dataclass
class TokenUsage:
    """What an answer actually cost, filled in by the adapter as it streams.

    Mutable and passed *in* rather than returned, because `stream_answer`
    yields text and the cost is not known until the provider's final chunk --
    the same shape `AnswerStream.cited_labels` already uses for the same
    reason. A caller that does not care about cost passes nothing and the
    adapter does not ask the provider for it.

    `total` stays 0 when the provider reported nothing, which is deliberately
    indistinguishable from "cost nothing": both mean there is nothing to bill
    against a budget, and inventing an estimate would be a number nobody could
    reconcile with their provider invoice.

    **The split is normalised at the adapter boundary.** Every provider names
    these differently (OpenAI `prompt`/`completion`, Anthropic `input`/
    `output`), and the adapter maps its own vocabulary onto these two so the
    quota store, the API and the console all speak one language and adding a
    provider does not ripple outward.

    `total` is carried rather than derived, because a provider's reported total
    can exceed the sum: reasoning and cached tokens are billed and do not
    always appear in either half. Recomputing it would under-count the bill,
    which is the one direction a spending control must never err in.
    """

    total: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def billable(self) -> int:
        return self.total or (self.input_tokens + self.output_tokens)


class ChatModel(Protocol):
    """Streams a grounded answer.

    Streams rather than returns: an answer grounded in five passages takes
    seconds to generate, and a visitor watching a blank box for five seconds
    assumes it is broken. Token-by-token delivery is the difference between
    "slow" and "not working".
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
        """`model_name`/`model_parameters` are optional overrides of the
        adapter's own configured defaults -- omitted, the platform-wide model
        answers exactly as before these existed. `AnswerQuestion` supplies
        them only when the caller named an assistant whose model was already
        chosen and entitlement-checked ahead of time; nothing else can reach
        this parameter.

        `usage`, when given, is filled in with what the answer cost. Passing
        it is what makes the adapter *ask* the provider to report cost at all,
        so a caller with no budget to enforce sends exactly the request it
        sent before this parameter existed.

        `credential_ciphertext` is the tenant's own provider key, **still
        encrypted**. The plaintext deliberately never crosses this boundary:
        `CredentialEncryptor`'s contract says only the AI-execution
        infrastructure decrypts, at model-call time, so the application layer
        moves the secret without ever being able to read it. Omitted, the call
        uses the platform's configured key, exactly as before."""
        ...


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
        session_id: UUID | None = None,
    ) -> Any: ...

    def verify(self, token: str) -> Any: ...

    def read_resumable(self, token: str) -> Any:
        """The claims of a previous token, ignoring expiry, or `None`.

        Separate from `verify` on purpose: this answers "which conversation
        was this browser part of?" at mint time, where an expired token is
        still a truthful answer, while `verify` guards every request that
        *acts* and must keep refusing one.
        """
        ...


# ---------------------------------------------------------------------------
# Entitlements, chatbot configuration, teams and per-tenant quota.
# ---------------------------------------------------------------------------


class TenantEntitlementRepository(Protocol):
    """What a tenant is allowed to have. Platform-written, tenant-readable."""

    async def get_for_tenant(self, tenant_id: UUID) -> TenantEntitlements | None:
        """`None` when the platform has never configured this tenant.

        Returned rather than defaulted here so the *caller* decides what an
        unconfigured tenant means -- `resolve_entitlements` applies the
        documented defaults, and a read-only screen can honestly show "not
        configured" instead of inventing numbers that were never set.
        """
        ...

    async def upsert(self, entitlements: TenantEntitlements) -> None: ...

    async def list_all(self) -> list[TenantEntitlements]:
        """Every configured tenant, for the platform console."""
        ...

    async def count_knowledge_bases(self, tenant_id: UUID) -> int:
        """Live count, not a stored one.

        A denormalised counter would drift the first time a knowledge base was
        removed by anything that forgot to decrement it, and a drifted quota
        counter fails in whichever direction is worse at the time.
        """
        ...

    async def count_chat_widgets(self, tenant_id: UUID) -> int: ...

    async def count_assistants(self, tenant_id: UUID) -> int: ...


class TenantChatbotSettingsRepository(Protocol):
    async def get_for_tenant(self, tenant_id: UUID) -> TenantChatbotSettings | None: ...

    async def upsert(self, settings: TenantChatbotSettings) -> None: ...


class TenantTeamRepository(Protocol):
    async def get(self, *, tenant_id: UUID, team_id: UUID) -> TenantTeam | None: ...

    async def list_for_tenant(
        self, tenant_id: UUID, *, active_only: bool = False
    ) -> list[TenantTeam]:
        """`active_only` is what the visitor-facing handoff menu asks for: a
        deactivated team must not be offered, but must still be readable so
        conversations already routed to it keep rendering its name."""
        ...

    async def add(self, team: TenantTeam) -> None: ...

    async def save(self, team: TenantTeam) -> None: ...

    async def list_members(self, *, tenant_id: UUID, team_id: UUID) -> list[UUID]:
        """Membership ids staffing this team."""
        ...

    async def list_memberships_with_permission(
        self, *, tenant_id: UUID, permission_code: str
    ) -> list[UUID]:
        """Memberships whose granted roles carry this permission code.

        On the team repository because its only caller is notification
        fan-out, which needs "who has oversight of the queue" alongside "who
        staffs this team" and would otherwise reach into the tenant-authz unit
        of work from an AI-resources use case.

        Resolved from live role grants, never cached: revoking the permission
        has to stop the notifications too, or a former supervisor keeps being
        alerted about queues they can no longer open.
        """
        ...

    async def list_team_ids_for_membership(
        self, *, tenant_id: UUID, membership_id: UUID
    ) -> list[UUID]:
        """The teams this member staffs -- the agent's queue scope.

        Answered in SQL rather than by listing every team and filtering: the
        set that decides what an agent may see must not depend on the caller
        remembering to filter, and a tenant may run many teams.
        """
        ...

    async def set_members(
        self, *, tenant_id: UUID, team_id: UUID, membership_ids: list[UUID]
    ) -> None: ...

    async def teams_for_membership(
        self, *, tenant_id: UUID, membership_id: UUID
    ) -> list[UUID]:
        """Which teams an agent belongs to -- their Unassigned inbox scope."""
        ...


class TenantQuotaStore(Protocol):
    """Per-tenant AI spending, in atomic counters.

    Two deliberately different shapes; see
    `infrastructure/cache/tenant_quota.py` for the full reasoning. Messages
    reserve up front (cost known: one). Tokens read before and record after
    (cost unknown until the provider answers).
    """

    async def consume_message(
        self, *, tenant_id: UUID, limit: int | None, zone: tzinfo | None = None
    ) -> bool:
        """Atomically reserves one AI message; False means over the limit.

        Atomic because the alternative -- read the count, then write it --
        loses under concurrency in the direction that costs money: two requests
        both read 999 against a limit of 1000 and both proceed.
        """
        ...

    async def release_message(
        self, *, tenant_id: UUID, zone: tzinfo | None = None
    ) -> None:
        """Returns a reservation for AI work that never happened."""
        ...

    async def messages_used_today(
        self, *, tenant_id: UUID, zone: tzinfo | None = None
    ) -> int:
        """**Must be given the same zone the writes used**, or the number
        shown and the number enforced are different days."""
        ...

    async def tokens_used_this_month(self, *, tenant_id: UUID) -> int:
        """Fails closed: raises rather than returning 0 when unreadable."""
        ...

    async def token_breakdown(self, *, tenant_id: UUID) -> TokenUsage: ...

    async def record_tokens(self, *, tenant_id: UUID, usage: TokenUsage) -> None: ...


class PushSubscriptionRepository(Protocol):
    """Browsers that may be notified for one tenant."""

    async def upsert(self, subscription: PushSubscription) -> None:
        """Stores or refreshes a subscription.

        Upsert rather than insert: a browser re-subscribing hands back the
        *same* endpoint, and treating that as a conflict would either error on
        an ordinary page reload or accumulate duplicates that each get their
        own copy of every notification.
        """
        ...

    async def list_for_memberships(
        self, *, tenant_id: UUID, membership_ids: Sequence[UUID]
    ) -> list[PushSubscription]:
        """Subscriptions for exactly these memberships.

        Takes the recipient list rather than resolving it: who *should* be
        notified is an authorization decision (it must honour the same team
        scope the inbox does), and deciding it in a repository would put it
        somewhere no permission check can see.
        """
        ...

    async def delete_for_membership(
        self, *, tenant_id: UUID, membership_id: UUID, endpoint: str
    ) -> int:
        """An agent turning notifications off for one of *their own* browsers.

        Scoped to the membership as well as the tenant, and that is the whole
        difference from `delete_by_endpoint`: an endpoint string learned any
        other way must not silence a colleague's notifications, which would be
        a quiet denial of service against one agent that nothing would report.

        Two methods rather than one with an optional `membership_id` -- an
        argument whose absence widens authority is the kind that gets dropped
        by a later edit and nobody notices.
        """
        ...

    async def delete_by_endpoint(self, *, tenant_id: UUID, endpoint: str) -> int:
        """Prunes an endpoint the push service has reported permanently gone.

        Not membership-scoped because there is no acting agent: the push
        service said 404/410, so whichever row holds that endpoint is dead.
        Never called from a request path.
        """
        ...

    async def mark_used(self, *, tenant_id: UUID, endpoint: str, at: datetime) -> None: ...


class WebPushSender(Protocol):
    """Delivers one encrypted payload to one browser.

    Narrow on purpose: one subscription, one message. Fan-out belongs in the
    use case, where a failure for one agent can be handled without abandoning
    the rest.
    """

    @property
    def is_configured(self) -> bool:
        """False when no VAPID keypair is set. Callers skip sending entirely
        rather than attempting it -- an unconfigured deployment should do
        nothing, not log a failure per agent per handoff."""
        ...

    async def send(
        self, *, subscription: PushSubscription, message: PushMessage
    ) -> PushSendResult: ...


class PushSendOutcome(StrEnum):
    DELIVERED = "delivered"
    #: The push service says this endpoint is permanently gone (404/410). The
    #: subscription should be deleted -- retrying it forever is the shape of
    #: bug that makes a notification system slowly cost more and do less.
    EXPIRED = "expired"
    #: Anything else: a timeout, a 5xx, a rate limit. Kept, not pruned.
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class PushSendResult:
    outcome: PushSendOutcome
    detail: str | None = None


class ConversationEventPublisher(Protocol):
    """Fan-out for realtime console updates.

    **This platform has no WebSocket layer** -- its realtime transport is
    Server-Sent Events, already used for streamed answers and already proven to
    pass through the console's BFF proxy unbuffered. Adding a WebSocket stack
    for one-way server-to-client notifications would be a second realtime
    system to secure, authenticate and operate, for a direction SSE already
    covers. Redis pub/sub carries events between API processes so a notice
    reaches an agent whichever worker holds their stream.
    """

    async def publish(self, *, tenant_id: UUID, event: str, payload: dict[str, Any]) -> None: ...

    def subscribe(self, *, tenant_id: UUID) -> AsyncGenerator[dict[str, Any]]:
        """Events for one tenant. The tenant scope is the isolation boundary
        and is derived from the authenticated session, never from the client.

        Typed as an `AsyncGenerator`, not an `AsyncIterator`, because callers
        must be able to `aclose()` it -- that is what unsubscribes and returns
        the Redis connection to the pool, and `AsyncIterator` does not declare
        it. Every implementation here is already a generator.
        """
        ...
