"""Request/response DTOs for the AI-resource endpoints.

Two shapes here exist specifically to enforce boundaries the domain can't:

- ``ProviderCredentialResponse`` has no ciphertext field, matching
  ``ProviderCredentialSummary`` -- see docs/16-schema-ai-resources.md.
- ``ConversationSummaryResponse`` vs ``ConversationResponse``: a non-owner who
  reached a conversation via ``tenant.conversations.view`` gets the former
  (metadata only), per docs/16's auditor rule.

Note what is *absent* from the request models: no ``vector_namespace`` on
``CreateKnowledgeBaseRequest`` and no ``storage_path`` on
``UploadDocumentRequest``. Both are server-derived; there is deliberately no
field for a client to populate.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl

Visibility = Literal["tenant", "department", "team", "restricted"]
AccessLevel = Literal["viewer", "editor", "owner"]


class CreateAssistantRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    model_configuration_id: UUID
    visibility: Visibility = "tenant"
    department_id: UUID | None = None
    team_id: UUID | None = None
    system_prompt: str | None = None


class CreateAssistantResponse(BaseModel):
    id: UUID


class AssistantResponse(BaseModel):
    id: UUID
    name: str
    description: str | None
    visibility: Visibility
    department_id: UUID | None
    team_id: UUID | None
    owner_membership_id: UUID
    model_configuration_id: UUID
    system_prompt: str | None
    status: Literal["draft", "published", "archived"]
    created_at: datetime
    updated_at: datetime


class AssistantListResponse(BaseModel):
    assistants: list[AssistantResponse]


class UpdateAssistantRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    model_configuration_id: UUID
    system_prompt: str | None = None


class ChangeVisibilityRequest(BaseModel):
    visibility: Visibility
    department_id: UUID | None = None
    team_id: UUID | None = None


class ModelConfigurationResponse(BaseModel):
    """One model this tenant may assign.

    Deliberately narrow. `tenant_id` and `is_platform_default` used to be here
    so the console could grey out rows it was not allowed to assign; every row
    returned now *is* assignable, so both fields would only invite a client to
    re-derive an availability rule the server already applied. Who owns a
    configuration is platform information and is not a tenant's business.
    """

    id: UUID
    model_name: str
    #: The monthly cap on this model, if the platform set one, and what this
    #: tenant has spent against it. Shown to the tenant deliberately -- unlike
    #: `tenant_id`/`is_platform_default`, which were removed because they only
    #: invited a client to re-derive a server-side rule, a budget is something
    #: the tenant *hits*: without it the first sign of a cap is a 429 mid-answer.
    token_budget_per_month: int | None = None
    #: `None` means the counter could not be read -- never rendered as 0, which
    #: would claim nothing has been spent.
    tokens_used_this_month: int | None = None
    #: This tenant's own provider key for this model, if they attached one.
    #: `None` means the platform's key answers and the platform is billed --
    #: the default for every grant. Only the id: a `key_hint` belongs to the
    #: credential list, and the ciphertext belongs nowhere near a response.
    provider_credential_id: UUID | None = None


class SetModelCredentialRequest(BaseModel):
    """Attach a credential, or detach with an explicit null.

    Only an id: the plaintext key is never accepted here. It enters once,
    through the credential-creation endpoint, and is envelope-encrypted
    immediately -- letting a second route take a raw secret would be a second
    place for one to be logged.
    """

    provider_credential_id: UUID | None = None


class ModelConfigurationListResponse(BaseModel):
    model_configurations: list[ModelConfigurationResponse]


class GrantAssistantAccessRequest(BaseModel):
    membership_id: UUID
    access_level: AccessLevel = "viewer"


class GrantAssistantAccessResponse(BaseModel):
    id: UUID


class CreateKnowledgeBaseRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    visibility: Visibility = "tenant"
    department_id: UUID | None = None
    team_id: UUID | None = None


class CreateKnowledgeBaseResponse(BaseModel):
    id: UUID


class KnowledgeBaseResponse(BaseModel):
    id: UUID
    name: str
    description: str | None
    visibility: Visibility
    owner_membership_id: UUID
    created_at: datetime


class KnowledgeBaseListResponse(BaseModel):
    knowledge_bases: list[KnowledgeBaseResponse]


class UploadDocumentRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=500)
    content_type: str
    size_bytes: int = Field(gt=0)
    checksum: str


class UploadDocumentResponse(BaseModel):
    id: UUID


class DocumentResponse(BaseModel):
    id: UUID
    filename: str
    content_type: str
    size_bytes: int
    status: Literal["processing", "ready", "failed"]
    #: Populated only when status='failed'. Surfaced so a tenant can fix a bad
    #: upload themselves rather than opening a support ticket.
    failure_reason: str | None
    #: How many searchable chunks this document actually produced.
    #:
    #: `status` says whether the pipeline finished; this says whether it
    #: produced anything to find. The two can disagree, and when they did --
    #: `ready` with zero chunks, from a scanned PDF whose OCR ran out of
    #: memory -- nothing in the console showed it. That is what this is for.
    chunk_count: int
    created_at: datetime


class DocumentListResponse(BaseModel):
    documents: list[DocumentResponse]


class DocumentChunkResponse(BaseModel):
    """One indexed passage, as stored.

    No embedding: 3072 floats per chunk would dominate the response and mean
    nothing to a reader. No score either -- that only exists relative to a
    query, and this is the document, not an answer.
    """

    id: UUID
    chunk_index: int
    text: str
    token_count: int
    #: "page 7", "row 12", or the page URL for a crawled document.
    source_location: str | None


class DocumentDetailResponse(BaseModel):
    document: DocumentResponse
    #: The requested page of chunks, in document order.
    chunks: list[DocumentChunkResponse]
    #: Total for the document, so a caller knows how many are not on this page.
    chunk_count: int
    #: Present for a crawled page, absent for an uploaded file. Lets the
    #: console link back to the page a document came from.
    source_url: str | None


class CreateDataSourceRequest(BaseModel):
    """A URL or website crawl to feed a knowledge base.

    Note what is absent: no depth, no page budget, no timeout. Those are
    platform limits (`CRAWL__*`), not tenant input -- they exist to bound what
    this platform will spend on one tenant's behalf, so letting the tenant set
    them would defeat their only purpose.
    """

    urls: list[HttpUrl] = Field(min_length=1, max_length=50)
    #: `url_list` fetches exactly these; `site` follows links from the one
    #: starting URL, bounded by the platform's depth and page limits.
    mode: Literal["url_list", "site"] = "url_list"


class CreateDataSourceResponse(BaseModel):
    id: UUID


class DataSourceResponse(BaseModel):
    id: UUID
    knowledge_base_id: UUID
    urls: list[str]
    mode: Literal["url_list", "site"]
    sync_status: Literal["idle", "syncing", "ready", "error"]
    #: Populated only when sync_status='error'.
    failure_reason: str | None
    pages_discovered: int
    pages_indexed: int
    last_synced_at: datetime | None
    created_at: datetime


class DataSourceListResponse(BaseModel):
    data_sources: list[DataSourceResponse]


class QueryKnowledgeBaseRequest(BaseModel):
    query_text: str = Field(min_length=1)
    top_k: int = Field(default=10, ge=1, le=100)


class CreateChatWidgetRequest(BaseModel):
    """A public question-answering widget for one knowledge base.

    `allowed_origins` is required with at least one entry: a widget with an
    empty allowlist can never mint a session, and storing one would be a
    silently useless row.
    """

    knowledge_base_id: UUID
    name: str = Field(min_length=1, max_length=200)
    allowed_origins: list[HttpUrl] = Field(min_length=1, max_length=20)
    #: Bounded here as well as in the database: this is what the deployment
    #: spends on the tenant's behalf when the widget is on a busy page.
    daily_question_limit: int = Field(default=500, ge=1, le=100_000)


class ChatWidgetResponse(BaseModel):
    id: UUID
    knowledge_base_id: UUID
    name: str
    #: Returned in full, unlike a provider credential. This one is public by
    #: construction -- it ships in a script tag -- and the tenant needs to copy
    #: it out to embed the widget.
    public_key: str
    allowed_origins: list[str]
    status: Literal["active", "disabled"]
    daily_question_limit: int
    created_at: datetime
    #: Built server-side from `public_api_base_url`. The console has no way to
    #: derive it (no NEXT_PUBLIC backend origin exists, by design), and one
    #: builder means the snippet a tenant copies cannot drift from the URL the
    #: script is actually served at.
    embed_snippet: str


class SetChatWidgetStatusRequest(BaseModel):
    enabled: bool


class ChatWidgetListResponse(BaseModel):
    chat_widgets: list[ChatWidgetResponse]


class AnswerQuestionRequest(BaseModel):
    """A question to answer from this knowledge base.

    No `top_k`, no raw model name, no temperature: how many passages ground an
    answer and which model writes it are platform decisions, not free-form
    caller input. A caller who could raise `top_k` could raise this
    deployment's per-question cost at will.

    `assistant_id` is the one exception, and only in appearance: it does not
    let the caller name a model directly, it lets them point at one of *their
    own tenant's* assistants -- a resource whose model was already chosen and
    entitlement-checked ahead of time by whoever created it, and which the
    visibility check refuses to resolve for anyone else's. Omit it and the
    platform default model answers, exactly as it did before this field
    existed.
    """

    question: str = Field(min_length=1, max_length=2000)
    assistant_id: UUID | None = None


class SearchHitResponse(BaseModel):
    document_id: UUID
    filename: str
    score: float


class QueryKnowledgeBaseResponse(BaseModel):
    hits: list[SearchHitResponse]


class StartConversationRequest(BaseModel):
    assistant_id: UUID
    title: str | None = None


class StartConversationResponse(BaseModel):
    id: UUID


class ConversationResponse(BaseModel):
    id: UUID
    assistant_id: UUID
    membership_id: UUID
    title: str | None
    status: Literal["active", "archived"]
    created_at: datetime
    last_message_at: datetime | None


class ConversationSummaryResponse(BaseModel):
    """What a non-owner auditor sees: no title, since a conversation title is
    user-authored content, not metadata (docs/16 -- auditors get metadata, not
    message content, and a title routinely leaks the subject matter)."""

    id: UUID
    assistant_id: UUID
    membership_id: UUID
    status: Literal["active", "archived"]
    created_at: datetime
    last_message_at: datetime | None


class ConversationListResponse(BaseModel):
    conversations: list[ConversationResponse]


class StoreProviderCredentialRequest(BaseModel):
    provider: str = Field(min_length=1, max_length=100)
    secret: str = Field(min_length=1)


class RotateProviderCredentialRequest(BaseModel):
    new_secret: str = Field(min_length=1)


class ProviderCredentialResponse(BaseModel):
    """No ciphertext field, by construction -- see the module docstring."""

    id: UUID
    provider: str
    key_hint: str
    created_at: datetime
    rotated_at: datetime | None
    revoked_at: datetime | None


class ProviderCredentialListResponse(BaseModel):
    credentials: list[ProviderCredentialResponse]
