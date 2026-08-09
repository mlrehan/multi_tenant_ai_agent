"""SQLAlchemy models for the security/audit tables needed by authentication.

Only the four tables the identity module writes to are modeled here:
``audit_logs``, ``security_events``, ``login_attempts``, ``account_lockouts``.
The remaining Security & Audit domain tables from docs/17-schema-security-audit.md
(``access_reviews``, ``impersonation_sessions``, ``policy_decisions``) depend
on tenant/platform authorization concepts that don't exist yet and are added
in the phase that implements those.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import CITEXT, INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from iam_platform.core.ids import uuid7
from iam_platform.infrastructure.db.base import Base


class AuditLogModel(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        CheckConstraint("result IN ('success','denied','error')", name="result_valid"),
        Index("ix_audit_logs_tenant_occurred", "tenant_id", "occurred_at"),
        Index("ix_audit_logs_actor_occurred", "actor_user_id", "occurred_at"),
        Index("ix_audit_logs_action", "action"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)
    occurred_at: Mapped[datetime] = mapped_column(server_default=func.now())
    # SET NULL, not CASCADE: a user's audit trail must outlive their row --
    # deletion is soft (docs/11-schema-global-identity.md) and the eventual
    # anonymization job scrubs the users row's PII rather than removing it,
    # but the FK is still SET NULL as a defensive backstop.
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    effective_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    # No FK yet -- `tenants` doesn't exist until the tenancy module lands.
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    action: Mapped[str] = mapped_column(Text, nullable=False)
    resource_type: Mapped[str | None] = mapped_column(Text)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    before_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    result: Mapped[str] = mapped_column(Text, nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    correlation_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    impersonation_session_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)


class SecurityEventModel(Base):
    __tablename__ = "security_events"
    __table_args__ = (
        CheckConstraint("severity IN ('info','warning','critical')", name="severity_valid"),
        Index("ix_security_events_severity_occurred", "severity", "occurred_at"),
        Index("ix_security_events_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)
    occurred_at: Mapped[datetime] = mapped_column(server_default=func.now())
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None]
    resolved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )


class LoginAttemptModel(Base):
    __tablename__ = "login_attempts"
    __table_args__ = (
        CheckConstraint(
            "result IN ('success','invalid_credentials','locked','mfa_failed')",
            name="result_valid",
        ),
        Index("ix_login_attempts_email_occurred", "email_attempted", "occurred_at"),
        Index("ix_login_attempts_ip_occurred", "ip", "occurred_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)
    occurred_at: Mapped[datetime] = mapped_column(server_default=func.now())
    email_attempted: Mapped[str] = mapped_column(CITEXT, nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    result: Mapped[str] = mapped_column(Text, nullable=False)
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)


class AccountLockoutModel(Base):
    __tablename__ = "account_lockouts"
    __table_args__ = (Index("ix_account_lockouts_user_id", "user_id"),)

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    locked_at: Mapped[datetime]
    unlock_at: Mapped[datetime | None]
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    failed_attempt_count: Mapped[int] = mapped_column(nullable=False)
    unlocked_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    unlocked_at: Mapped[datetime | None]
