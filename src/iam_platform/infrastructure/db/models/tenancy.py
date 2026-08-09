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
