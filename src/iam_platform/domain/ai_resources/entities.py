"""AI-resource domain entities -- see docs/16-schema-ai-resources.md.

``integrations`` and ``data_sources`` are intentionally not modeled yet -- see
the Phase 7 scope note (CLAUDE.md): external-system sync is an ops
integration with no bearing on the authorization model this phase exists to
prove. Message-level conversation content is likewise deferred; docs/16 left
its storage shape to be decided here, and the decision is that it belongs
with AI serving, not with authorization.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from iam_platform.domain.shared.entity import Entity
from iam_platform.domain.shared.exceptions import InvalidStateTransitionError


class ResourceVisibility(StrEnum):
    TENANT = "tenant"
    DEPARTMENT = "department"
    TEAM = "team"
    RESTRICTED = "restricted"


class AssistantStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


@dataclass(kw_only=True)
class AiAssistant(Entity):
    tenant_id: UUID
    name: str
    description: str | None = None
    visibility: ResourceVisibility = ResourceVisibility.TENANT
    department_id: UUID | None = None
    team_id: UUID | None = None
    owner_membership_id: UUID
    model_configuration_id: UUID
    status: AssistantStatus = AssistantStatus.DRAFT
    system_prompt: str | None = None
    created_at: datetime
    updated_at: datetime

    def publish(self, *, now: datetime) -> None:
        if self.status == AssistantStatus.ARCHIVED:
            raise InvalidStateTransitionError("cannot publish an archived assistant")
        self.status = AssistantStatus.PUBLISHED
        self.updated_at = now

    def unpublish(self, *, now: datetime) -> None:
        if self.status != AssistantStatus.PUBLISHED:
            raise InvalidStateTransitionError(f"cannot unpublish from status {self.status}")
        self.status = AssistantStatus.DRAFT
        self.updated_at = now

    def archive(self, *, now: datetime) -> None:
        if self.status == AssistantStatus.ARCHIVED:
            raise InvalidStateTransitionError("assistant already archived")
        self.status = AssistantStatus.ARCHIVED
        self.updated_at = now

    def update_details(
        self,
        *,
        name: str,
        description: str | None,
        system_prompt: str | None,
        model_configuration_id: UUID,
        now: datetime,
    ) -> None:
        self.name = name
        self.description = description
        self.system_prompt = system_prompt
        self.model_configuration_id = model_configuration_id
        self.updated_at = now

    def change_visibility(
        self,
        *,
        visibility: ResourceVisibility,
        department_id: UUID | None,
        team_id: UUID | None,
        now: datetime,
    ) -> None:
        # A department/team-scoped resource without its scoping column set
        # would silently fall back to "nobody matches" in the visibility
        # policy -- reject it here so the invalid state can never be stored.
        if visibility == ResourceVisibility.DEPARTMENT and department_id is None:
            raise InvalidStateTransitionError("department visibility requires a department_id")
        if visibility == ResourceVisibility.TEAM and team_id is None:
            raise InvalidStateTransitionError("team visibility requires a team_id")
        self.visibility = visibility
        self.department_id = department_id
        self.team_id = team_id
        self.updated_at = now


class AssistantAccessLevel(StrEnum):
    VIEWER = "viewer"
    EDITOR = "editor"
    OWNER = "owner"


@dataclass(kw_only=True)
class AssistantMember(Entity):
    tenant_id: UUID
    assistant_id: UUID
    membership_id: UUID
    access_level: AssistantAccessLevel = AssistantAccessLevel.VIEWER
    added_at: datetime


@dataclass(kw_only=True)
class KnowledgeBase(Entity):
    tenant_id: UUID
    name: str
    description: str | None = None
    owner_membership_id: UUID
    visibility: ResourceVisibility = ResourceVisibility.TENANT
    department_id: UUID | None = None
    team_id: UUID | None = None
    # Server-generated as `{tenant_id}/{id}`, never accepted as client input
    # (docs/16-schema-ai-resources.md) -- the concrete mechanism behind the
    # mandatory server-injected tenant filter on every vector query.
    vector_namespace: str
    created_at: datetime
    updated_at: datetime


class DocumentStatus(StrEnum):
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


@dataclass(kw_only=True)
class Document(Entity):
    tenant_id: UUID
    knowledge_base_id: UUID
    uploaded_by_membership_id: UUID
    filename: str
    content_type: str
    # Server-generated as `{tenant_id}/{knowledge_base_id}/{document_id}`,
    # same never-client-suppliable rule as `vector_namespace`.
    storage_path: str
    size_bytes: int
    status: DocumentStatus = DocumentStatus.PROCESSING
    checksum: str
    created_at: datetime
    deleted_at: datetime | None = None
    #: Why ingestion failed. Set only alongside FAILED, and cleared on a
    #: successful re-ingest so a stale reason can't outlive the failure.
    failure_reason: str | None = None

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def mark_ready(self) -> None:
        if self.status != DocumentStatus.PROCESSING:
            raise InvalidStateTransitionError(f"cannot mark ready from status {self.status}")
        self.status = DocumentStatus.READY
        self.failure_reason = None

    def mark_failed(self, *, reason: str | None = None) -> None:
        if self.status != DocumentStatus.PROCESSING:
            raise InvalidStateTransitionError(f"cannot mark failed from status {self.status}")
        self.status = DocumentStatus.FAILED
        self.failure_reason = reason

    def soft_delete(self, *, now: datetime) -> None:
        if self.deleted_at is not None:
            raise InvalidStateTransitionError("document already deleted")
        self.deleted_at = now


class DataSourceKind(StrEnum):
    """Where a knowledge base's content comes from.

    `UPLOAD` exists in the vocabulary because docs/16 defines it, but uploaded
    documents do not currently create a `data_sources` row -- they are their
    own record. Kept so the CHECK constraint and this enum agree, and so an
    upload source can be introduced later without a migration.
    """

    UPLOAD = "upload"
    URL_CRAWL = "url_crawl"
    INTEGRATION_SYNC = "integration_sync"


class SyncStatus(StrEnum):
    IDLE = "idle"
    SYNCING = "syncing"
    READY = "ready"
    ERROR = "error"


class CrawlMode(StrEnum):
    """What a crawl source means by its list of URLs.

    Mirrors `application.ai_resources.ports.CrawlMode`. Duplicated rather than
    imported because `domain` has zero project-internal imports by contract
    (docs/20) -- the domain cannot depend on the application layer, and this
    concept genuinely belongs to both.
    """

    URL_LIST = "url_list"
    SITE = "site"


@dataclass(kw_only=True)
class DataSource(Entity):
    """A crawl feeding a knowledge base.

    The URLs live on the entity rather than in an opaque blob so the invariants
    below are enforceable at all: a `url_crawl` with no URLs is a row that can
    never do anything, and a `SITE` crawl of five different start URLs is five
    crawls wearing one row's status field.
    """

    tenant_id: UUID
    knowledge_base_id: UUID
    kind: DataSourceKind
    urls: list[str]
    mode: CrawlMode
    created_by_membership_id: UUID
    sync_status: SyncStatus = SyncStatus.IDLE
    failure_reason: str | None = None
    pages_discovered: int = 0
    pages_indexed: int = 0
    last_synced_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if self.kind is DataSourceKind.URL_CRAWL and not self.urls:
            raise ValueError("a url_crawl data source needs at least one URL")
        if self.mode is CrawlMode.SITE and len(self.urls) != 1:
            # A site crawl follows links from *a* starting point. Several start
            # URLs would be several crawls sharing one status and one page
            # budget, and neither could be reported honestly.
            raise ValueError("a site crawl takes exactly one starting URL")

    def mark_syncing(self) -> None:
        self.sync_status = SyncStatus.SYNCING
        self.failure_reason = None
        self.pages_discovered = 0
        self.pages_indexed = 0

    def mark_ready(self, *, discovered: int, indexed: int, at: datetime) -> None:
        self.sync_status = SyncStatus.READY
        self.failure_reason = None
        self.pages_discovered = discovered
        self.pages_indexed = indexed
        self.last_synced_at = at

    def mark_failed(self, *, reason: str) -> None:
        self.sync_status = SyncStatus.ERROR
        self.failure_reason = reason


class WidgetStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


@dataclass(kw_only=True)
class ChatWidget(Entity):
    """A public question-answering surface for one knowledge base.

    The origin check lives here rather than in the route because it is a
    *policy* decision about what this widget permits, and putting it in the
    domain means every caller gets it -- including ones added later.
    """

    tenant_id: UUID
    knowledge_base_id: UUID
    name: str
    #: Public by construction: it sits in a script tag on a public page. Not a
    #: credential, and nothing here treats it as one.
    public_key: str
    allowed_origins: list[str]
    status: WidgetStatus = WidgetStatus.ACTIVE
    daily_question_limit: int = 500
    created_by_membership_id: UUID
    created_at: datetime
    updated_at: datetime

    @property
    def is_active(self) -> bool:
        return self.status is WidgetStatus.ACTIVE

    def permits_origin(self, origin: str | None) -> bool:
        """Exact, case-insensitive match against the allowlist.

        **No wildcards and no suffix matching**, deliberately. `*.example.com`
        looks convenient and is how origin checks get broken: a naive
        `endswith(".example.com")` also accepts `evil-example.com`, and getting
        wildcard matching right is a parsing problem nobody needs to have. A
        tenant with three subdomains lists three origins.

        An empty allowlist permits nothing. A widget that has not been told
        where it may run is unusable rather than usable everywhere -- the
        opposite default would make a half-configured widget a public endpoint
        for anyone who read its key.

        **What this is worth:** browsers set `Origin` and page JavaScript
        cannot forge it, so this genuinely stops another *website* embedding
        the widget. It does not stop a non-browser client sending any origin it
        likes. Against that, the real defences are the rate limit and the daily
        cap, not this check -- see docs/03-threat-model.md.
        """
        if origin is None or not self.allowed_origins:
            return False
        candidate = origin.strip().rstrip("/").lower()
        return any(
            allowed.strip().rstrip("/").lower() == candidate
            for allowed in self.allowed_origins
        )

    def disable(self) -> None:
        self.status = WidgetStatus.DISABLED

    def enable(self) -> None:
        self.status = WidgetStatus.ACTIVE


class ConversationStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


@dataclass(kw_only=True)
class Conversation(Entity):
    tenant_id: UUID
    assistant_id: UUID
    membership_id: UUID
    title: str | None = None
    status: ConversationStatus = ConversationStatus.ACTIVE
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime | None = None

    def archive(self, *, now: datetime) -> None:
        if self.status == ConversationStatus.ARCHIVED:
            raise InvalidStateTransitionError("conversation already archived")
        self.status = ConversationStatus.ARCHIVED
        self.updated_at = now


@dataclass(kw_only=True)
class ModelConfiguration(Entity):
    tenant_id: UUID | None  # None => platform-provided default, readable by all tenants
    provider_credential_id: UUID | None = None
    model_name: str
    parameters: dict[str, Any] = field(default_factory=dict)
    token_budget_per_month: int | None = None
    created_at: datetime
    updated_at: datetime

    @property
    def is_platform_default(self) -> bool:
        return self.tenant_id is None


class CredentialOwnerType(StrEnum):
    PLATFORM = "platform"
    TENANT = "tenant"


@dataclass(kw_only=True)
class ProviderCredential(Entity):
    """The ciphertext lives on this entity but is never returned by any read
    API -- only ``key_hint`` is ever surfaced (docs/16-schema-ai-resources.md).
    Enforcing that is the API layer's job; what the domain guarantees is that
    a revoked credential can never be handed to a model call.
    """

    owner_type: CredentialOwnerType
    tenant_id: UUID | None  # required iff owner_type == TENANT
    provider: str
    credential_ciphertext: bytes
    key_hint: str
    created_by_user_id: UUID
    created_at: datetime
    rotated_at: datetime | None = None
    revoked_at: datetime | None = None

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None

    def rotate(self, *, ciphertext: bytes, key_hint: str, now: datetime) -> None:
        if self.revoked_at is not None:
            raise InvalidStateTransitionError("cannot rotate a revoked credential")
        self.credential_ciphertext = ciphertext
        self.key_hint = key_hint
        self.rotated_at = now

    def revoke(self, *, now: datetime) -> None:
        if self.revoked_at is not None:
            raise InvalidStateTransitionError("credential already revoked")
        self.revoked_at = now
