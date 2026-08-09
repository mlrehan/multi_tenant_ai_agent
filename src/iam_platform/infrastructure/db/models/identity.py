"""SQLAlchemy 2.0 models for the global-identity schema -- docs/11-schema-global-identity.md.

All twelve tables from that doc are modeled here so the Alembic migration is
schema-complete; only the subset the Phase 5 use cases actually touch
(``users``, ``identities``, ``credentials``, ``oauth_accounts``,
``email_verifications``, ``password_reset_tokens``, ``mfa_methods``,
``sessions``, ``refresh_tokens``) has a repository implementation in
``repositories/identity.py``. ``user_profiles``, ``trusted_devices``, and
``api_keys`` are modeled now and get repositories/use cases in a later phase
(profile management, device trust, API-key issuance are not core
authentication flows).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    LargeBinary,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, CITEXT, INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from iam_platform.core.ids import uuid7
from iam_platform.infrastructure.db.base import Base, TimestampMixin


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)


class UserModel(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending_verification','active','suspended','deactivated')",
            name="status_valid",
        ),
        Index("ix_users_status", "status"),
    )

    id: Mapped[uuid.UUID] = _pk()
    email: Mapped[str] = mapped_column(CITEXT, unique=True, nullable=False)
    email_verified_at: Mapped[datetime | None]
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending_verification")
    security_stamp: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), default=uuid7)
    last_login_at: Mapped[datetime | None]
    deleted_at: Mapped[datetime | None]


class UserProfileModel(TimestampMixin, Base):
    __tablename__ = "user_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    display_name: Mapped[str | None]
    given_name: Mapped[str | None]
    family_name: Mapped[str | None]
    avatar_url: Mapped[str | None]
    locale: Mapped[str] = mapped_column(Text, default="en")
    timezone: Mapped[str] = mapped_column(Text, default="UTC")
    phone_number: Mapped[str | None]
    phone_verified_at: Mapped[datetime | None]
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)


class AuthIdentityModel(TimestampMixin, Base):
    __tablename__ = "identities"
    __table_args__ = (
        CheckConstraint("kind IN ('password','oauth','webauthn')", name="kind_valid"),
        Index("ix_identities_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    last_used_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]


class CredentialModel(Base):
    __tablename__ = "credentials"

    id: Mapped[uuid.UUID] = _pk()
    identity_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("identities.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    password_algo: Mapped[str] = mapped_column(Text, default="argon2id")
    password_updated_at: Mapped[datetime]


class OAuthAccountModel(Base):
    __tablename__ = "oauth_accounts"
    __table_args__ = (UniqueConstraint("provider", "provider_subject", name="provider_subject"),)

    id: Mapped[uuid.UUID] = _pk()
    identity_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("identities.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    provider_subject: Mapped[str] = mapped_column(Text, nullable=False)
    provider_email: Mapped[str | None] = mapped_column(CITEXT)
    access_token_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)
    refresh_token_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)
    raw_profile: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    linked_at: Mapped[datetime]


class EmailVerificationModel(Base):
    __tablename__ = "email_verifications"
    __table_args__ = (
        CheckConstraint("purpose IN ('register','email_change')", name="purpose_valid"),
        Index("ix_email_verifications_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    new_email: Mapped[str | None] = mapped_column(CITEXT)
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)


class PasswordResetTokenModel(Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)


class MfaMethodModel(Base):
    __tablename__ = "mfa_methods"
    __table_args__ = (
        CheckConstraint("type IN ('totp','webauthn','sms_backup')", name="type_valid"),
    )

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(Text, nullable=False)
    secret_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)
    webauthn_credential_id: Mapped[bytes | None] = mapped_column(LargeBinary)
    webauthn_public_key: Mapped[bytes | None] = mapped_column(LargeBinary)
    sign_count: Mapped[int] = mapped_column(default=0)
    label: Mapped[str | None] = mapped_column(Text)
    is_primary: Mapped[bool] = mapped_column(default=False)
    verified_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_used_at: Mapped[datetime | None]
    disabled_at: Mapped[datetime | None]


class TrustedDeviceModel(Base):
    __tablename__ = "trusted_devices"

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    device_fingerprint_hash: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str | None] = mapped_column(Text)
    ip_first_seen: Mapped[str | None] = mapped_column(INET)
    ip_last_seen: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
    trusted_until: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    revoked_at: Mapped[datetime | None]


class SessionModel(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        Index("ix_sessions_user_id", "user_id"),
        Index("ix_sessions_revoked_at", "revoked_at"),
    )

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_seen_at: Mapped[datetime]
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
    security_stamp_snapshot: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True))
    # Deliberate addition beyond the original docs/11 spec: lets a refreshed
    # access token truthfully reissue `amr` including "mfa" without persisting
    # a full AMR history -- see application/identity/refresh_session.py.
    mfa_verified: Mapped[bool] = mapped_column(default=False)
    revoked_at: Mapped[datetime | None]
    revoked_reason: Mapped[str | None] = mapped_column(Text)


class RefreshTokenModel(Base):
    __tablename__ = "refresh_tokens"
    __table_args__ = (
        # Kept as an explicit allow-list rather than free text: `revoked_reason`
        # is read during incident review, and "why did this session end?" is
        # only answerable if the values are a closed, meaningful set.
        # `password_change`, `account_suspended`, `account_deleted` and
        # `email_changed` were added when administrative account lifecycle
        # landed -- each is a materially different reason to kill a session and
        # collapsing them into the generic 'admin' would lose that.
        CheckConstraint(
            "revoked_reason IS NULL OR revoked_reason IN "
            "('rotated','reuse_detected','logout','logout_all','password_reset',"
            "'password_change','account_suspended','account_deleted','email_changed','admin')",
            name="revoked_reason_valid",
        ),
        Index("ix_refresh_tokens_family_id", "family_id"),
        Index("ix_refresh_tokens_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    family_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(server_default=func.now())
    expires_at: Mapped[datetime]
    rotated_at: Mapped[datetime | None]
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("refresh_tokens.id")
    )
    revoked_at: Mapped[datetime | None]
    revoked_reason: Mapped[str | None] = mapped_column(Text)


class ApiKeyModel(Base):
    __tablename__ = "api_keys"
    __table_args__ = (
        CheckConstraint("owner_type IN ('platform','tenant')", name="owner_type_valid"),
        CheckConstraint(
            "(owner_type = 'tenant' AND tenant_id IS NOT NULL) OR "
            "(owner_type = 'platform' AND tenant_id IS NULL)",
            name="tenant_consistency",
        ),
        Index("ix_api_keys_tenant_id", "tenant_id"),
    )

    id: Mapped[uuid.UUID] = _pk()
    owner_type: Mapped[str] = mapped_column(Text, nullable=False)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    key_prefix: Mapped[str] = mapped_column(Text, nullable=False)
    key_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    last_used_at: Mapped[datetime | None]
    expires_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
