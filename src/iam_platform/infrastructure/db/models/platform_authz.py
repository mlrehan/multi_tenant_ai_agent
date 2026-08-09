"""SQLAlchemy models for the platform-authorization schema --
docs/12-schema-platform-authorization.md -- plus `impersonation_sessions`
(docs/17-schema-security-audit.md), grouped here since it's platform-owned.

None of these tables get an RLS policy for `app_tenant` -- they're enabled +
forced (so even a superuser can't accidentally bypass it) with no policy
created, which is a default-deny; only `app_platform` (BYPASSRLS) and
migrations can touch them. See docs/18-schema-rls-and-migrations.md.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, Index, Text, text
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from iam_platform.core.ids import uuid7
from iam_platform.infrastructure.db.base import Base, TimestampMixin


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)


class PlatformRoleModel(TimestampMixin, Base):
    __tablename__ = "platform_roles"

    id: Mapped[uuid.UUID] = _pk()
    code: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_system: Mapped[bool] = mapped_column(default=True)
    rank: Mapped[int] = mapped_column(nullable=False)


class PlatformPermissionModel(Base):
    __tablename__ = "platform_permissions"
    __table_args__ = (
        CheckConstraint("scope = 'platform'", name="scope_valid"),
        CheckConstraint("risk_level IN ('low','medium','high','critical')", name="risk_level_valid"),
    )

    id: Mapped[uuid.UUID] = _pk()
    code: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    scope: Mapped[str] = mapped_column(Text, nullable=False, default="platform")
    resource: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    risk_level: Mapped[str] = mapped_column(Text, nullable=False, default="low")
    is_system: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(server_default="now()")


class PlatformRolePermissionModel(Base):
    __tablename__ = "platform_role_permissions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["role_id"],
            ["platform_roles.id"],
            name="fk_platform_role_permissions_role_id",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["permission_id"],
            ["platform_permissions.id"],
            name="fk_platform_role_permissions_permission_id",
            ondelete="CASCADE",
        ),
    )

    role_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    permission_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    granted_at: Mapped[datetime] = mapped_column(server_default="now()")


class PlatformUserRoleModel(Base):
    __tablename__ = "platform_user_roles"
    __table_args__ = (
        Index(
            "uq_platform_user_roles_active",
            "user_id",
            "role_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
        Index("ix_platform_user_roles_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("platform_roles.id"), nullable=False
    )
    granted_by_user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    granted_at: Mapped[datetime] = mapped_column(server_default="now()")
    revoked_at: Mapped[datetime | None]
    revoked_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id")
    )


class ImpersonationSessionModel(Base):
    __tablename__ = "impersonation_sessions"
    __table_args__ = (
        CheckConstraint(
            "approval_status IN ('not_required','pending','approved','denied')",
            name="approval_status_valid",
        ),
        Index("ix_impersonation_sessions_platform_user_id", "platform_user_id"),
        Index("ix_impersonation_sessions_target_user_id", "target_user_id"),
    )

    id: Mapped[uuid.UUID] = _pk()
    platform_user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    target_user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    approval_status: Mapped[str] = mapped_column(Text, nullable=False, default="not_required")
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id")
    )
    started_at: Mapped[datetime] = mapped_column(server_default="now()")
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    ended_at: Mapped[datetime | None]
    ip: Mapped[str | None] = mapped_column(INET)
    session_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
