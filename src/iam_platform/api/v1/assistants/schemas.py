"""Request/response DTOs for the AI-resource endpoints.

One shape here exists specifically to enforce a boundary the domain can't:

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
from typing import Any, Literal
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
    #: The guided brief the chatbot console edits. Returned so an edit form is
    #: populated with what is stored -- the failure `AssistantResponse` already
    #: had once, when `system_prompt` was missing and every save silently
    #: overwrote the existing prompt with an empty string.
    role_instructions: str | None
    avoid_instructions: str | None
    personality: str
    response_length: str
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


class UpdateChatWidgetRequest(BaseModel):
    """The operational half of a widget: who may embed it, and how much it may
    spend. Presentation (avatar, greeting) is edited elsewhere.

    No `public_key` and no `knowledge_base_id`: the key is embedded in script
    tags on sites this console does not control, so changing it would silently
    break every page already carrying it.
    """

    name: str = Field(min_length=1, max_length=200)
    allowed_origins: list[HttpUrl] = Field(min_length=1, max_length=20)
    daily_question_limit: int = Field(default=500, ge=1, le=100_000)


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
    #: Continue an existing thread: its history is loaded into the prompt and
    #: this exchange is appended to it. Ownership is re-checked server-side, so
    #: supplying someone else's id gets a 404, never their history.
    conversation_id: UUID | None = None


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
    #: Both nullable since widget conversations gained persistence: a visitor
    #: thread has no assistant bound (widget-to-assistant binding is a later,
    #: separate feature) and is owned by `visitor_session_id`, not a member.
    id: UUID
    assistant_id: UUID | None
    membership_id: UUID | None
    title: str | None
    status: Literal["active", "archived"]
    created_at: datetime
    last_message_at: datetime | None


class ConversationMessageResponse(BaseModel):
    """One turn, as shown when a thread is reopened."""

    id: UUID
    seq: int
    #: Mirrors `MessageRole` in domain/ai_resources/entities.py: `user`/
    #: `assistant` are the visitor and the AI, `agent_message`/
    #: `internal_comment` are a human agent's reply and staff-only note, and
    #: `system_event` marks a transfer (e.g. "Conversation transferred...").
    role: Literal["user", "assistant", "agent_message", "internal_comment", "system_event"]
    content: str
    #: What this answer actually cited -- label, document and location. Only
    #: the sources the model used, not every candidate that was offered.
    citations: list[dict[str, Any]] = []
    #: The exchange's token cost, recorded on the answer that incurred it.
    #: 0 on a user turn and on any answer the provider did not report usage for.
    token_count: int = 0
    created_at: datetime


class ConversationMessagesResponse(BaseModel):
    conversation: ConversationResponse
    #: False when the caller reached this through the oversight permission --
    #: the console uses it to show a "viewing another member's conversation"
    #: banner and to hide the owner-only rename and delete controls.
    is_owner: bool
    messages: list[ConversationMessageResponse]
    #: The visitor is composing something right now. Ephemeral cache state, not
    #: a turn -- it is deliberately absent from `messages`, because it is not
    #: something anybody said and has no place in a transcript.
    #:
    #: Defaulted so every other caller of this response shape is unaffected.
    visitor_typing: bool = False
    #: Turns in the whole thread, not in this page.
    total_messages: int = 0
    #: Whether older turns exist above this page.
    has_more: bool = False


class RenameConversationRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class ConversationSummaryResponse(BaseModel):
    """What a non-owner auditor sees: no title, since a conversation title is
    user-authored content, not metadata (docs/16 -- auditors get metadata, not
    message content, and a title routinely leaks the subject matter)."""

    id: UUID
    assistant_id: UUID | None
    membership_id: UUID | None
    status: Literal["active", "archived"]
    created_at: datetime
    last_message_at: datetime | None


class ConversationListResponse(BaseModel):
    conversations: list[ConversationResponse]


# Provider-credential schemas were removed with the tenant-facing BYOK
# surface: the platform owns every credential now, so a tenant has nothing to
# store, rotate, revoke or attach and therefore no request or response shape
# to carry one.
