"""SQLAlchemy models for the tenancy schema -- docs/13-schema-tenant-management.md.

`tenant_domains`, `tenant_settings`, `tenant_subscriptions`, and
`tenant_usage_limits` are deferred (Phase 6 scope note, CLAUDE.md).
`tenants` itself is not tenant-owned (no RLS); every other table here is.
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
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, CITEXT, JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from iam_platform.core.ids import uuid7
from iam_platform.infrastructure.db.base import Base, TimestampMixin


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)


class TenantModel(TimestampMixin, Base):
    __tablename__ = "tenants"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','active','suspended','deactivated')", name="status_valid"
        ),
        Index("ix_tenants_status", "status"),
    )

    id: Mapped[uuid.UUID] = _pk()
    slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    region: Mapped[str | None] = mapped_column(Text)
    suspended_at: Mapped[datetime | None]
    suspended_reason: Mapped[str | None] = mapped_column(Text)
    deleted_at: Mapped[datetime | None]


class TenantMembershipModel(TimestampMixin, Base):
    __tablename__ = "tenant_memberships"
    __table_args__ = (
        CheckConstraint(
            "status IN ('invited','active','suspended','revoked')", name="status_valid"
        ),
        UniqueConstraint("tenant_id", "user_id", name="uq_tenant_memberships_tenant_id_user_id"),
        UniqueConstraint("tenant_id", "id", name="uq_tenant_memberships_tenant_id_id"),
        Index(
            "ux_tenant_memberships_one_default_per_user",
            "user_id",
            unique=True,
            postgresql_where=text("is_default"),
        ),
        Index("ix_tenant_memberships_tenant_id", "tenant_id"),
        Index("ix_tenant_memberships_user_id", "user_id"),
        Index("ix_tenant_memberships_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, default="invited")
    is_default: Mapped[bool] = mapped_column(default=False)
    department_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    team_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    job_title: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    invited_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id")
    )
    invited_at: Mapped[datetime | None]
    joined_at: Mapped[datetime | None]
    last_activity_at: Mapped[datetime | None]
    suspended_at: Mapped[datetime | None]
    suspended_reason: Mapped[str | None] = mapped_column(Text)
    revoked_at: Mapped[datetime | None]
    revoked_reason: Mapped[str | None] = mapped_column(Text)


class TenantInvitationModel(Base):
    __tablename__ = "tenant_invitations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','accepted','revoked','expired')", name="status_valid"
        ),
        UniqueConstraint("token_hash", name="uq_tenant_invitations_token_hash"),
        Index(
            "ux_tenant_invitations_pending_per_email",
            "tenant_id",
            "email",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
        Index("ix_tenant_invitations_tenant_id", "tenant_id"),
    )

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(CITEXT, nullable=False)
    invited_by_user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    role_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(PgUUID(as_uuid=True)), default=list)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    department_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    team_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    accepted_at: Mapped[datetime | None]
    accepted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id")
    )
    created_at: Mapped[datetime] = mapped_column(server_default="now()")


class TenantFeatureModel(Base):
    __tablename__ = "tenant_features"
    __table_args__ = (
        UniqueConstraint("tenant_id", "feature_code", name="uq_tenant_features_tenant_id_feature_code"),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_tenant_features_tenant_id_tenants", ondelete="CASCADE"
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    feature_code: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(default=True)
    source: Mapped[str] = mapped_column(Text, nullable=False, default="override")
    granted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id")
    )
    created_at: Mapped[datetime] = mapped_column(server_default="now()")


class TenantEntitlementModel(TimestampMixin, Base):
    """Platform-set ceilings and capability flags for one tenant.

    **Not tenant-writable, and the grant is what enforces that** -- see the
    migration, which gives `app_tenant` SELECT only. A tenant admin reading
    their own limits is legitimate (the console shows "2 of 3 knowledge bases
    used"); a tenant admin *raising* them would defeat the entire mechanism,
    and RLS alone cannot express "readable but not writable".
    """

    __tablename__ = "tenant_entitlements"
    __table_args__ = (
        # One row per tenant. A second row would mean two answers to "what is
        # this tenant allowed to do", and whichever the query happened to
        # return would be the limit -- a race decided by row order.
        UniqueConstraint("tenant_id", name="uq_tenant_entitlements_tenant"),
        CheckConstraint(
            "max_knowledge_bases IS NULL OR max_knowledge_bases >= 0",
            name="max_knowledge_bases_nonneg",
        ),
        CheckConstraint(
            "max_chat_widgets IS NULL OR max_chat_widgets >= 0",
            name="max_chat_widgets_nonneg",
        ),
        CheckConstraint(
            "max_messages_per_day IS NULL OR max_messages_per_day >= 0",
            name="max_messages_per_day_nonneg",
        ),
        CheckConstraint(
            "max_tokens_per_month IS NULL OR max_tokens_per_month >= 0",
            name="max_tokens_per_month_nonneg",
        ),
        Index("ix_tenant_entitlements_tenant_id", "tenant_id"),
    )

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    #: NULL => uncapped. Deliberately distinct from 0, which means "none".
    max_knowledge_bases: Mapped[int | None]
    max_chat_widgets: Mapped[int | None]
    max_messages_per_day: Mapped[int | None]
    max_tokens_per_month: Mapped[int | None] = mapped_column(BigInteger)
    allow_own_provider_credentials: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("false")
    )
    allow_create_assistant: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("false")
    )
    allow_invite_members: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("false")
    )
    allow_create_roles: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("false")
    )
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id")
    )


class TenantTeamModel(TimestampMixin, Base):
    """A team a conversation can be routed to. Names come from the tenant."""

    __tablename__ = "tenant_teams"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_tenant_teams_name"),
        # The FK target for `conversations.(tenant_id, assigned_team_id)`.
        # Postgres refuses that composite FK without this -- the fifth time
        # this codebase has hit the rule (documents, data_sources,
        # model_configurations, chat_widgets, now here).
        UniqueConstraint("tenant_id", "id", name="uq_tenant_teams_tenant_id_id"),
        Index("ix_tenant_teams_tenant_id", "tenant_id"),
    )

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))


class TenantTeamMemberModel(Base):
    """Who staffs a team.

    Composite FK to `tenant_memberships`, so a membership belonging to another
    tenant cannot be added to this tenant's team whatever id a request
    carries -- the isolation is Postgres's, not the use case's.
    """

    __tablename__ = "tenant_team_members"
    __table_args__ = (
        UniqueConstraint("team_id", "membership_id", name="uq_tenant_team_members_pair"),
        ForeignKeyConstraint(
            ["tenant_id", "team_id"],
            ["tenant_teams.tenant_id", "tenant_teams.id"],
            name="fk_tenant_team_members_team",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "membership_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.id"],
            name="fk_tenant_team_members_membership",
            ondelete="CASCADE",
        ),
        Index("ix_tenant_team_members_tenant_id", "tenant_id"),
        Index("ix_tenant_team_members_team_id", "team_id"),
    )

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    team_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    membership_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))


class PushSubscriptionModel(Base):
    """A browser this platform can notify while the console is closed.

    Keyed to a *membership*, not a user: one browser holds one push endpoint
    per origin, but a person may staff two tenants, and each tenant must
    notify only about its own queue. The composite FK is what makes that
    Postgres's guarantee rather than the use case's.
    """

    __tablename__ = "push_subscriptions"
    __table_args__ = (
        UniqueConstraint(
            "membership_id", "endpoint", name="uq_push_subscriptions_membership_endpoint"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "membership_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.id"],
            name="fk_push_subscriptions_membership",
            ondelete="CASCADE",
        ),
        Index("ix_push_subscriptions_tenant_id", "tenant_id"),
        Index("ix_push_subscriptions_membership_id", "membership_id"),
    )

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    membership_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    endpoint: Mapped[str] = mapped_column(Text(), nullable=False)
    p256dh_key: Mapped[str] = mapped_column(Text(), nullable=False)
    auth_key: Mapped[str] = mapped_column(Text(), nullable=False)
    user_agent: Mapped[str | None] = mapped_column(Text(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    last_used_at: Mapped[datetime | None] = mapped_column(nullable=True)


class TenantChatbotSettingsModel(TimestampMixin, Base):
    """Company-wide chatbot policy and context. One row per tenant.

    Lives here rather than in `ai_resources.py` because it is keyed to the
    tenant and shares the entitlement table's lifecycle -- but unlike
    entitlements it *is* tenant-writable: these are the tenant's own decisions
    about their bot, not the platform's ceiling on them. The one field where
    the two meet is `daily_message_limit`, which the domain clamps to the
    platform maximum on read as well as write, so a row written before that
    check existed still cannot raise the cap.
    """

    __tablename__ = "tenant_chatbot_settings"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_tenant_chatbot_settings_tenant"),
        CheckConstraint(
            "length(company_description) <= 2000", name="company_description_bounded"
        ),
        CheckConstraint("length(industry) <= 100", name="industry_bounded"),
        CheckConstraint(
            "daily_message_limit IS NULL OR daily_message_limit >= 0",
            name="daily_message_limit_nonneg",
        ),
        Index("ix_tenant_chatbot_settings_tenant_id", "tenant_id"),
    )

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    #: The master switch. False must keep visitors out of RAG, the model and
    #: both quotas -- not merely hide the AI's replies.
    ai_chatbot_enabled: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("true")
    )
    #: Chatbot-facing only; `tenants.display_name` remains account identity.
    company_name: Mapped[str | None] = mapped_column(Text)
    company_description: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("''")
    )
    industry: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'Early Years Education and Childcare (UK Nursery / Preschool)'"),
    )
    allow_human_handoff: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("true")
    )
    add_ai_summary_as_internal_comment: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("false")
    )
    allow_ai_for_unassigned_conversations: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("true")
    )
    #: NULL => inherit the platform ceiling; a value can only ever lower it.
    daily_message_limit: Mapped[int | None]
    share_visitor_location: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("true")
    )
    conversation_retention_days: Mapped[int] = mapped_column(
        nullable=False, server_default=text("30")
    )
