"""SQLAlchemy models for the AI-resource schema -- docs/16-schema-ai-resources.md.

`integrations` and `data_sources` are deferred (Phase 7 scope note, CLAUDE.md).

Two additions beyond that doc, both following precedents already set in this
codebase:

- `knowledge_bases.department_id`/`team_id`: docs/16 gives `knowledge_bases` a
  `visibility` column with the same four modes as `ai_assistants` but no
  columns to scope department/team visibility against -- an omission, since
  `visibility='department'` is otherwise unsatisfiable. Added to match
  `ai_assistants`.
- `documents.deleted_at` participates in the unique index on `storage_path`
  via a partial index, so a soft-deleted document doesn't block re-uploading
  the same logical path.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from iam_platform.core.ids import uuid7
from iam_platform.infrastructure.db.base import Base, TimestampMixin

_VISIBILITY_VALUES = "'tenant','department','team','restricted'"


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)


class ModelConfigurationModel(TimestampMixin, Base):
    """A model the platform offers. Ownership and *availability* are separate
    things here, and only the first lives on this row.

    `tenant_id IS NULL` means platform-owned; a non-null value means the row
    was created for one tenant (the shape this table shipped with, still in
    use). Neither says which tenants may *use* it -- that is
    `tenant_model_configurations`, and it is what `ai_assistants` references.
    """

    __tablename__ = "model_configurations"
    __table_args__ = (
        # Kept: `tenant_model_configurations` references (tenant_id, id) for
        # tenant-owned rows, and a composite FK needs a matching UNIQUE on the
        # referenced side -- the lesson this codebase has now learned three
        # times (documents, data_sources, and here).
        UniqueConstraint("tenant_id", "id", name="uq_model_configurations_tenant_id_id"),
    )

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE")
    )
    provider_credential_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    model_name: Mapped[str] = mapped_column(Text, nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    token_budget_per_month: Mapped[int | None] = mapped_column(BigInteger)
    #: Withdrawn from new assignments. Not a delete -- assistants already
    #: using it keep working.
    archived_at: Mapped[datetime | None]


class TenantModelConfigurationModel(Base):
    """Which model configurations a tenant is allowed to use.

    **This table is the authorization boundary, not a convenience index.**
    `ai_assistants` carries a composite FK to `(tenant_id,
    model_configuration_id)` here, so an assistant cannot reference a
    configuration its tenant has not been granted -- the database refuses it
    regardless of what the application layer believes. That is strictly
    stronger than the constraint it replaced, which only enforced "the
    configuration belongs to my tenant" and therefore made platform-owned
    configurations unusable by anyone.

    The same FK gives revocation its policy for free: with no ON DELETE
    action, Postgres refuses to delete an entitlement row while an assistant
    still depends on it, so a tenant cannot be left pointing at a
    configuration it may no longer use.
    """

    __tablename__ = "tenant_model_configurations"
    __table_args__ = (
        # The FK target from `ai_assistants`, and the "granted once" rule.
        UniqueConstraint(
            "tenant_id",
            "model_configuration_id",
            name="uq_tenant_model_configurations_pair",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_tenant_model_configurations_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["model_configuration_id"],
            ["model_configurations.id"],
            name="fk_tenant_model_configurations_model_configuration",
            # A configuration cannot be hard-deleted out from under a grant;
            # archiving is the supported way to retire one.
            ondelete="RESTRICT",
        ),
        Index("ix_tenant_model_configurations_tenant_id", "tenant_id"),
        Index(
            "ix_tenant_model_configurations_model_configuration_id",
            "model_configuration_id",
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    model_configuration_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), nullable=False
    )
    granted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))


class ProviderCredentialModel(Base):
    __tablename__ = "provider_credentials"
    __table_args__ = (
        CheckConstraint("owner_type IN ('platform','tenant')", name="owner_type_valid"),
        CheckConstraint(
            "(owner_type = 'platform' AND tenant_id IS NULL) OR "
            "(owner_type = 'tenant' AND tenant_id IS NOT NULL)",
            name="tenant_consistency",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_provider_credentials_tenant_id_id"),
        Index("ix_provider_credentials_tenant_id", "tenant_id"),
    )

    id: Mapped[uuid.UUID] = _pk()
    owner_type: Mapped[str] = mapped_column(Text, nullable=False)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE")
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    credential_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_hint: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    rotated_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]


class AiAssistantModel(TimestampMixin, Base):
    __tablename__ = "ai_assistants"
    __table_args__ = (
        CheckConstraint(f"visibility IN ({_VISIBILITY_VALUES})", name="visibility_valid"),
        CheckConstraint(
            "status IN ('draft','published','archived')", name="status_valid"
        ),
        # The domain entity enforces this too (change_visibility); duplicating
        # it here means bad data can't arrive via a migration, a fixture, or a
        # future code path that bypasses the entity.
        CheckConstraint(
            "(visibility <> 'department' OR department_id IS NOT NULL) AND "
            "(visibility <> 'team' OR team_id IS NOT NULL)",
            name="visibility_scope_consistency",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "owner_membership_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.id"],
            name="fk_ai_assistants_owner_membership",
        ),
        # Points at the *entitlement*, not at the configuration. This is what
        # makes "a tenant may only use models it has been granted" a database
        # invariant rather than an application convention -- a hand-crafted
        # request carrying another tenant's configuration id is rejected by
        # Postgres even if every check above it were wrong.
        ForeignKeyConstraint(
            ["tenant_id", "model_configuration_id"],
            [
                "tenant_model_configurations.tenant_id",
                "tenant_model_configurations.model_configuration_id",
            ],
            name="fk_ai_assistants_model_configuration",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_ai_assistants_tenant_id_id"),
        Index("ix_ai_assistants_tenant_id", "tenant_id"),
        Index("ix_ai_assistants_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    visibility: Mapped[str] = mapped_column(Text, nullable=False, default="tenant")
    department_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    team_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    owner_membership_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    model_configuration_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="draft")
    system_prompt: Mapped[str | None] = mapped_column(Text)


class AssistantMemberModel(Base):
    __tablename__ = "assistant_members"
    __table_args__ = (
        CheckConstraint("access_level IN ('viewer','editor','owner')", name="access_level_valid"),
        ForeignKeyConstraint(
            ["tenant_id", "assistant_id"],
            ["ai_assistants.tenant_id", "ai_assistants.id"],
            name="fk_assistant_members_assistant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "membership_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.id"],
            name="fk_assistant_members_membership",
            ondelete="CASCADE",
        ),
        UniqueConstraint("assistant_id", "membership_id", name="uq_assistant_members_pair"),
        Index("ix_assistant_members_tenant_id", "tenant_id"),
        Index("ix_assistant_members_membership_id", "membership_id"),
    )

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    assistant_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    membership_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    access_level: Mapped[str] = mapped_column(Text, nullable=False, default="viewer")
    added_at: Mapped[datetime] = mapped_column(server_default=text("now()"))


class KnowledgeBaseModel(TimestampMixin, Base):
    __tablename__ = "knowledge_bases"
    __table_args__ = (
        CheckConstraint(f"visibility IN ({_VISIBILITY_VALUES})", name="visibility_valid"),
        CheckConstraint(
            "(visibility <> 'department' OR department_id IS NOT NULL) AND "
            "(visibility <> 'team' OR team_id IS NOT NULL)",
            name="visibility_scope_consistency",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "owner_membership_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.id"],
            name="fk_knowledge_bases_owner_membership",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_knowledge_bases_tenant_id_id"),
        UniqueConstraint("vector_namespace", name="uq_knowledge_bases_vector_namespace"),
        Index("ix_knowledge_bases_tenant_id", "tenant_id"),
    )

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    owner_membership_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    visibility: Mapped[str] = mapped_column(Text, nullable=False, default="tenant")
    department_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    team_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    vector_namespace: Mapped[str] = mapped_column(Text, nullable=False)


class DocumentModel(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("status IN ('processing','ready','failed')", name="status_valid"),
        ForeignKeyConstraint(
            ["tenant_id", "knowledge_base_id"],
            ["knowledge_bases.tenant_id", "knowledge_bases.id"],
            name="fk_documents_knowledge_base",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "uploaded_by_membership_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.id"],
            name="fk_documents_uploaded_by_membership",
        ),
        # Partial so a soft-deleted row doesn't permanently reserve its path.
        Index(
            "uq_documents_storage_path_live",
            "storage_path",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_documents_tenant_id", "tenant_id"),
        Index("ix_documents_knowledge_base_id", "knowledge_base_id"),
        # Required for `document_chunks`' composite FK to reference this table
        # -- Postgres needs a UNIQUE on the referenced columns. Matches the
        # `uq_*_tenant_id_id` constraint every other composite-FK target here
        # already carries.
        UniqueConstraint("tenant_id", "id", name="uq_documents_tenant_id_id"),
    )

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    uploaded_by_membership_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), nullable=False
    )
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="processing")
    checksum: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    deleted_at: Mapped[datetime | None]
    #: Why ingestion failed, when status='failed'. Surfaced to the tenant so a
    #: bad upload is self-diagnosable ("password-protected PDF") instead of an
    #: opaque red badge they have to open a support ticket about.
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Which crawl produced this page, for `url_crawl` documents; NULL for an
    #: uploaded file. `ON DELETE SET NULL`: removing a crawl source must not
    #: destroy content the tenant may still be relying on.
    data_source_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True
    )
    #: The page's address, for citation. Deliberately separate from
    #: `filename`: that is a display name, this is a link someone may follow.
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)


class DocumentChunkModel(Base):
    """Chunk provenance -- the record of truth for what was indexed.

    Qdrant is a *search index*, not the system of record: it can be dropped
    and rebuilt from these rows without re-parsing (or re-paying for) the
    original documents. That's also what makes an embedding-model change a
    re-embed rather than a full re-ingest, and it keeps the tenant-owned,
    RLS-governed copy of the text in Postgres where every other tenant-owned
    row already lives.
    """

    __tablename__ = "document_chunks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["documents.tenant_id", "documents.id"],
            name="fk_document_chunks_document",
            # Chunks are derived data with no independent meaning; when the
            # document row goes, they go. Contrast `documents` itself, which
            # RESTRICTs against `knowledge_bases` because a document is not
            # merely derived from its knowledge base.
            ondelete="CASCADE",
        ),
        # Re-ingesting a document must overwrite its chunks rather than
        # accumulate a second copy -- the same idempotency the Qdrant upsert
        # provides, enforced here at the database level too.
        Index(
            "uq_document_chunks_document_ordinal",
            "document_id",
            "chunk_index",
            unique=True,
        ),
        Index("ix_document_chunks_tenant_id", "tenant_id"),
        Index("ix_document_chunks_document_id", "document_id"),
        Index("ix_document_chunks_knowledge_base_id", "knowledge_base_id"),
    )

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    #: Denormalized from `documents` so a knowledge-base-scoped purge or
    #: re-index doesn't need a join, matching how `role_hierarchy` and
    #: `tenant_role_permissions` carry `tenant_id` (Phase 6).
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    document_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    chunk_index: Mapped[int] = mapped_column(nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(nullable=False)
    #: Human-meaningful origin within the document ("page 7", "Sheet1!A2:F2"),
    #: carried so a citation can point somewhere a person can actually look.
    source_location: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))


class DataSourceModel(TimestampMixin, Base):
    """A sync source feeding a knowledge base -- Phase 12's URL/website crawls.

    **This table's shape is a correction to docs/16, not an implementation of
    it.** The Phase 3 spec gives `data_sources` `kind`, `integration_id`,
    `knowledge_base_id`, `sync_status` and `last_synced_at` -- and nowhere to
    put *what to crawl*. `kind='url_crawl'` was therefore unsatisfiable as
    specified: no start URL, no depth, no page budget. The same class of gap as
    Phase 7's `knowledge_bases.visibility='department'` with no department
    column to scope against.

    `config` holds that, as JSONB, and holds **non-secret configuration only**
    -- the same rule docs/16 states for `integrations.config`. Nothing here is
    ever a credential; a crawl needing authentication would reference
    `provider_credentials` the way integrations do.

    `failure_reason` mirrors `documents`: a tenant who asks for a crawl and
    gets nothing needs to know whether the site refused them, the URL was
    unreachable, or this platform declined the target -- and needs it without
    reading a worker log they have no access to.
    """

    __tablename__ = "data_sources"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('upload','url_crawl','integration_sync')", name="kind_valid"
        ),
        CheckConstraint(
            "sync_status IN ('idle','syncing','ready','error')", name="sync_status_valid"
        ),
        # A crawl source without somewhere to crawl is meaningless, and a
        # constraint means a fixture or a hand-written migration cannot create
        # one either -- the domain entity enforces it too, but conventions
        # bypass and constraints do not. Same reasoning as the
        # department/team CHECK added to `ai_assistants` in Phase 7.
        CheckConstraint(
            "kind <> 'url_crawl' OR jsonb_array_length(config -> 'urls') > 0",
            name="url_crawl_needs_urls",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "knowledge_base_id"],
            ["knowledge_bases.tenant_id", "knowledge_bases.id"],
            name="fk_data_sources_knowledge_base",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by_membership_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.id"],
            name="fk_data_sources_created_by",
        ),
        # A composite FK needs a matching UNIQUE on the referenced side --
        # `documents.(tenant_id, data_source_id)` points here. Exactly the
        # constraint Phase 11 had to add to `documents` for `document_chunks`,
        # and Postgres refused this migration until it existed too.
        UniqueConstraint("tenant_id", "id", name="uq_data_sources_tenant_id_id"),
        Index("ix_data_sources_tenant_id", "tenant_id"),
        Index("ix_data_sources_knowledge_base_id", "knowledge_base_id"),
    )

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    #: Nullable per docs/16 -- an `integration_sync` source points at one, a
    #: crawl does not. No FK yet: `integrations` is still unbuilt (Phase 7
    #: deferred it), and a constraint referencing a nonexistent table cannot
    #: be created. Added when that table lands.
    integration_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True
    )
    #: Non-secret crawl configuration: `urls`, `mode`, and any per-source
    #: overrides of the platform crawl limits.
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    sync_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'idle'")
    )
    #: Populated only when sync_status='error'. Deliberately lossy, like
    #: `documents.failure_reason` -- the exception goes to the log.
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Rolling counters from the last run, for the console's status view.
    pages_discovered: Mapped[int] = mapped_column(nullable=False, server_default=text("0"))
    pages_indexed: Mapped[int] = mapped_column(nullable=False, server_default=text("0"))
    last_synced_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_by_membership_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), nullable=False
    )


class ChatWidgetModel(TimestampMixin, Base):
    """A public question-answering surface embedded on a tenant's own website.

    **`public_key` is not a secret and is not stored as one.** It ships inside
    a `<script>` tag on a public page, so anyone who can view source can read
    it. It is therefore stored in plaintext (hashing a value the whole internet
    can read protects nothing, and the console has to display it back), and it
    is not what authorizes an answer -- it only identifies which widget is
    asking. Contrast `provider_credentials`, where the value genuinely is a
    secret held on the tenant's behalf and never echoed back.

    What binds a widget to a site is `allowed_origins`; what bounds abuse is
    `daily_question_limit`.
    """

    __tablename__ = "chat_widgets"
    __table_args__ = (
        CheckConstraint("status IN ('active','disabled')", name="status_valid"),
        CheckConstraint("daily_question_limit > 0", name="limit_positive"),
        ForeignKeyConstraint(
            ["tenant_id", "knowledge_base_id"],
            ["knowledge_bases.tenant_id", "knowledge_bases.id"],
            name="fk_chat_widgets_knowledge_base",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by_membership_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.id"],
            name="fk_chat_widgets_created_by",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_chat_widgets_tenant_id_id"),
        Index("ix_chat_widgets_tenant_id", "tenant_id"),
    )

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    #: Globally unique -- the public endpoint is given only this, before any
    #: tenant is known, so it must identify one widget across every tenant.
    public_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    #: Empty means unusable, not open. Fail closed: the opposite default would
    #: turn a half-configured widget into a public endpoint for anyone who
    #: found its key.
    allowed_origins: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'"))
    #: Cost control. Each question is an embedding + a rerank + a generation,
    #: and the tenant who embedded the widget is not the one paying for it.
    daily_question_limit: Mapped[int] = mapped_column(
        nullable=False, server_default=text("500")
    )
    created_by_membership_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), nullable=False
    )


class ConversationModel(TimestampMixin, Base):
    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint("status IN ('active','archived')", name="status_valid"),
        ForeignKeyConstraint(
            ["tenant_id", "assistant_id"],
            ["ai_assistants.tenant_id", "ai_assistants.id"],
            name="fk_conversations_assistant",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "membership_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.id"],
            name="fk_conversations_membership",
        ),
        Index(
            "ix_conversations_tenant_membership",
            "tenant_id",
            "membership_id",
            text("last_message_at DESC"),
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    assistant_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    membership_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    last_message_at: Mapped[datetime | None]
