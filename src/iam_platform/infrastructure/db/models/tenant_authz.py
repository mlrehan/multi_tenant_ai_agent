"""SQLAlchemy models for the tenant-authorization schema --
docs/14-schema-tenant-authorization.md.

Two deliberate additions beyond that doc, both denormalizing a `tenant_id`
column onto a table that otherwise has no direct one (Phase 6 implementation
finding, mirrors the docs/11 `sessions.mfa_verified` amendment in Phase 5):

- `tenant_role_permissions.tenant_id` (nullable): lets the standard RLS
  template apply directly instead of a subquery-based policy joining through
  `tenant_roles`. NULL for system-role permission rows (shared, not
  RLS-restricted to a tenant).
- `role_hierarchy.tenant_id` (nullable): same reasoning -- an edge's tenant
  is otherwise only knowable by looking up its (possibly-NULL-tenant) roles.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from iam_platform.core.ids import uuid7
from iam_platform.infrastructure.db.base import Base, TimestampMixin


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)


class TenantRoleModel(TimestampMixin, Base):
    __tablename__ = "tenant_roles"
    __table_args__ = (
        CheckConstraint(
            "(tenant_id IS NULL AND is_system) OR (tenant_id IS NOT NULL AND NOT is_system)",
            name="system_consistency",
        ),
        # COALESCE bucket so every system role (tenant_id IS NULL) shares one
        # uniqueness namespace while custom roles are scoped per tenant --
        # docs/14-schema-tenant-authorization.md. A plain unique index on
        # (tenant_id, code) would NOT achieve this: Postgres treats every
        # NULL as distinct from every other NULL in a unique index, so two
        # system roles with the same code would NOT collide under a plain
        # index -- the expression index is what actually enforces it.
        Index(
            "uq_tenant_roles_code",
            text("COALESCE(tenant_id, '00000000-0000-0000-0000-000000000000'::uuid)"),
            "code",
            unique=True,
        ),
        UniqueConstraint("tenant_id", "id", name="uq_tenant_roles_tenant_id_id"),
    )

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE")
    )
    code: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_system: Mapped[bool] = mapped_column(default=False)
    rank: Mapped[int] = mapped_column(nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id")
    )


class TenantPermissionModel(Base):
    __tablename__ = "tenant_permissions"
    __table_args__ = (
        CheckConstraint("risk_level IN ('low','medium','high','critical')", name="risk_level_valid"),
        UniqueConstraint("code", name="uq_tenant_permissions_code"),
    )

    id: Mapped[uuid.UUID] = _pk()
    code: Mapped[str] = mapped_column(Text, nullable=False)
    resource: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    risk_level: Mapped[str] = mapped_column(Text, nullable=False, default="low")
    is_system: Mapped[bool] = mapped_column(default=True)
    tenant_customizable: Mapped[bool] = mapped_column(default=False)
    required_feature: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default="now()")


class TenantRolePermissionModel(Base):
    __tablename__ = "tenant_role_permissions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["role_id"], ["tenant_roles.id"], name="fk_tenant_role_permissions_role_id", ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["permission_id"],
            ["tenant_permissions.id"],
            name="fk_tenant_role_permissions_permission_id",
            ondelete="CASCADE",
        ),
    )

    role_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    permission_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    granted_at: Mapped[datetime] = mapped_column(server_default="now()")


class TenantMembershipRoleModel(Base):
    __tablename__ = "tenant_membership_roles"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "membership_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.id"],
            name="fk_tenant_membership_roles_membership",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["role_id"], ["tenant_roles.id"], name="fk_tenant_membership_roles_role_id"
        ),
        Index(
            "uq_tenant_membership_roles_active",
            "membership_id",
            "role_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
        Index("ix_tenant_membership_roles_tenant_id", "tenant_id"),
    )

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    membership_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    role_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    granted_by_user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    granted_at: Mapped[datetime] = mapped_column(server_default="now()")
    revoked_at: Mapped[datetime | None]


class RoleHierarchyModel(Base):
    __tablename__ = "role_hierarchy"
    __table_args__ = (
        CheckConstraint("role_scope IN ('platform','tenant')", name="role_scope_valid"),
        CheckConstraint("parent_role_id <> child_role_id", name="no_self_loop"),
        # Guarantees a platform-scope edge always has tenant_id NULL -- the
        # RLS policy on this table (docs/18-schema-rls-and-migrations.md)
        # depends on this to keep platform edges invisible to app_tenant: a
        # tenant-scope system-role edge ALSO has tenant_id NULL (shared
        # across all tenants), so the policy can't rely on tenant_id alone
        # and must check role_scope too -- this constraint is what makes
        # that check trustworthy rather than an unenforced convention.
        CheckConstraint(
            "(role_scope = 'platform' AND tenant_id IS NULL) OR role_scope = 'tenant'",
            name="platform_scope_has_no_tenant",
        ),
        UniqueConstraint("parent_role_id", "child_role_id", name="uq_role_hierarchy_edge"),
    )

    id: Mapped[uuid.UUID] = _pk()
    parent_role_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    child_role_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    role_scope: Mapped[str] = mapped_column(Text, nullable=False)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(server_default="now()")


class AuthorizationOverrideModel(Base):
    __tablename__ = "authorization_overrides"
    __table_args__ = (
        CheckConstraint("scope IN ('platform','tenant')", name="scope_valid"),
        CheckConstraint("subject_type IN ('membership','platform_user')", name="subject_type_valid"),
        CheckConstraint("effect IN ('allow','deny')", name="effect_valid"),
        CheckConstraint(
            "(scope = 'platform' AND tenant_id IS NULL AND subject_type = 'platform_user' "
            "AND platform_permission_id IS NOT NULL AND tenant_permission_id IS NULL) OR "
            "(scope = 'tenant' AND tenant_id IS NOT NULL AND subject_type = 'membership' "
            "AND tenant_permission_id IS NOT NULL AND platform_permission_id IS NULL)",
            name="scope_consistency",
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE")
    )
    subject_type: Mapped[str] = mapped_column(Text, nullable=False)
    subject_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    platform_permission_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("platform_permissions.id")
    )
    tenant_permission_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenant_permissions.id")
    )
    effect: Mapped[str] = mapped_column(Text, nullable=False)
    resource_type: Mapped[str | None] = mapped_column(Text)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    expires_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default="now()")
    revoked_at: Mapped[datetime | None]
