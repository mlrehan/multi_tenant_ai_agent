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
    id: UUID
    tenant_id: UUID | None
    model_name: str
    is_platform_default: bool


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
    created_at: datetime


class DocumentListResponse(BaseModel):
    documents: list[DocumentResponse]


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

    No `top_k`, no model name, no temperature: how many passages ground an
    answer and which model writes it are platform decisions, not caller input.
    A caller who could raise `top_k` could raise this deployment's per-question
    cost at will.
    """

    question: str = Field(min_length=1, max_length=2000)


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
