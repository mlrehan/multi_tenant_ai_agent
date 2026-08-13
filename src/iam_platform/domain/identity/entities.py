"""Identity domain entities -- see docs/11-schema-global-identity.md for the schema these mirror.

These are plain, persistence-ignorant objects: no SQLAlchemy, no FastAPI. The
infrastructure layer maps them to/from ORM rows; the application layer is the
only thing that calls their mutating methods.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from iam_platform.domain.shared.entity import Entity
from iam_platform.domain.shared.exceptions import InvalidStateTransitionError
from iam_platform.domain.shared.value_objects import Email


class UserStatus(StrEnum):
    PENDING_VERIFICATION = "pending_verification"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DEACTIVATED = "deactivated"


@dataclass(kw_only=True)
class User(Entity):
    email: Email
    email_verified_at: datetime | None = None
    status: UserStatus = UserStatus.PENDING_VERIFICATION
    security_stamp: UUID
    last_login_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None

    @property
    def is_active(self) -> bool:
        """Fully activated. Read by callers that need "a finished account",
        not by callers deciding whether a request may proceed -- see
        `can_authenticate`, which is a strictly weaker and different question.
        """
        return self.status == UserStatus.ACTIVE and self.deleted_at is None

    @property
    def can_authenticate(self) -> bool:
        """Whether this account may hold a session at all.

        Deliberately admits `PENDING_VERIFICATION`: this deployment's email
        sender only logs, so nobody can complete verification, and refusing
        unverified accounts would lock out every self-registered user with no
        way back. `mark_email_verified` promotes them if a real provider is
        ever wired in.

        **This exists because login and the per-request check disagreed.**
        `LoginUser` allowed a pending account through and the freshness check
        in `api/deps/authn.py` used `is_active`, which does not -- so a
        self-registered user signed in successfully and was then refused on
        every subsequent request with an opaque "session is no longer valid".
        Both paths now ask this one question, so they cannot drift apart
        again; that drift, not either rule on its own, was the bug.
        """
        return (
            self.status not in (UserStatus.SUSPENDED, UserStatus.DEACTIVATED)
            and self.deleted_at is None
        )

    def mark_email_verified(self, *, now: datetime) -> None:
        self.email_verified_at = now
        if self.status == UserStatus.PENDING_VERIFICATION:
            self.status = UserStatus.ACTIVE
        self.updated_at = now

    def record_login(self, *, now: datetime) -> None:
        self.last_login_at = now
        self.updated_at = now

    def bump_security_stamp(self, *, new_stamp: UUID, now: datetime) -> None:
        """Invalidates every outstanding access token's freshness check (logout-all,
        password change, security event) -- see docs/05-authentication-flows.md."""
        self.security_stamp = new_stamp
        self.updated_at = now

    def suspend(self, *, now: datetime) -> None:
        if self.status == UserStatus.DEACTIVATED:
            raise InvalidStateTransitionError("cannot suspend a deactivated user")
        self.status = UserStatus.SUSPENDED
        self.updated_at = now

    def reactivate(self, *, now: datetime) -> None:
        """Lifts a suspension.

        A deactivated (soft-deleted) account is deliberately not reactivatable
        here: restoring one is a data-recovery operation, not a status flip,
        and should be a deliberate act rather than a click."""
        if self.deleted_at is not None or self.status == UserStatus.DEACTIVATED:
            raise InvalidStateTransitionError("cannot reactivate a deactivated user")
        self.status = UserStatus.ACTIVE
        self.updated_at = now

    def soft_delete(self, *, now: datetime) -> None:
        """Marks the account deleted without destroying the row.

        Rows are never hard-deleted: `audit_logs` and `security_events`
        reference the actor, and an IAM system that can erase who did what
        defeats its own repudiation defenses (docs/03-threat-model.md)."""
        if self.deleted_at is not None:
            raise InvalidStateTransitionError("user is already deleted")
        self.deleted_at = now
        self.status = UserStatus.DEACTIVATED
        self.updated_at = now

    def change_email(self, *, new_email: Email, now: datetime) -> None:
        """Changes the login identifier.

        Verification is reset: the new address has not been proven to belong to
        this person, and leaving `email_verified_at` set would silently assert
        that it had."""
        if self.deleted_at is not None:
            raise InvalidStateTransitionError("cannot change a deleted user's email")
        self.email = new_email
        self.email_verified_at = None
        self.updated_at = now


class IdentityKind(StrEnum):
    PASSWORD = "password"
    OAUTH = "oauth"
    WEBAUTHN = "webauthn"


@dataclass(kw_only=True)
class AuthIdentity(Entity):
    """A row in ``identities`` -- one authentication *method* belonging to a user."""

    user_id: UUID
    kind: IdentityKind
    created_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None

    def revoke(self, *, now: datetime) -> None:
        self.revoked_at = now

    def record_use(self, *, now: datetime) -> None:
        self.last_used_at = now


@dataclass(kw_only=True)
class Credential(Entity):
    """Password secret for a ``password``-kind identity. ``password_hash`` is
    already-hashed ciphertext -- hashing is an infrastructure concern."""

    identity_id: UUID
    password_hash: str
    password_algo: str = "argon2id"
    password_updated_at: datetime

    def change_password(self, *, new_hash: str, now: datetime) -> None:
        self.password_hash = new_hash
        self.password_updated_at = now


@dataclass(kw_only=True)
class OAuthAccount(Entity):
    identity_id: UUID
    provider: str
    provider_subject: str
    provider_email: str | None = None
    raw_profile: dict[str, Any] = field(default_factory=dict)
    linked_at: datetime


class MfaMethodType(StrEnum):
    TOTP = "totp"
    WEBAUTHN = "webauthn"
    SMS_BACKUP = "sms_backup"


@dataclass(kw_only=True)
class MfaMethod(Entity):
    user_id: UUID
    type: MfaMethodType
    secret_encrypted: bytes | None = None
    webauthn_credential_id: bytes | None = None
    webauthn_public_key: bytes | None = None
    sign_count: int = 0
    label: str | None = None
    is_primary: bool = False
    verified_at: datetime | None = None
    created_at: datetime
    last_used_at: datetime | None = None
    disabled_at: datetime | None = None

    @property
    def is_usable(self) -> bool:
        return self.verified_at is not None and self.disabled_at is None

    def mark_verified(self, *, now: datetime) -> None:
        self.verified_at = now

    def record_use(self, *, now: datetime) -> None:
        self.last_used_at = now

    def disable(self, *, now: datetime) -> None:
        self.disabled_at = now


@dataclass(kw_only=True)
class Session(Entity):
    user_id: UUID
    created_at: datetime
    last_seen_at: datetime
    ip: str | None = None
    user_agent: str | None = None
    security_stamp_snapshot: UUID
    mfa_verified: bool = False
    revoked_at: datetime | None = None
    revoked_reason: str | None = None

    def is_valid(self, *, current_security_stamp: UUID) -> bool:
        return self.revoked_at is None and self.security_stamp_snapshot == current_security_stamp

    def revoke(self, *, reason: str, now: datetime) -> None:
        self.revoked_at = now
        self.revoked_reason = reason

    def touch(self, *, now: datetime) -> None:
        self.last_seen_at = now


@dataclass(kw_only=True)
class RefreshToken(Entity):
    user_id: UUID
    session_id: UUID
    family_id: UUID
    token_hash: str
    issued_at: datetime
    expires_at: datetime
    rotated_at: datetime | None = None
    replaced_by_id: UUID | None = None
    revoked_at: datetime | None = None
    revoked_reason: str | None = None

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None

    def is_expired(self, *, now: datetime) -> bool:
        return now >= self.expires_at

    @property
    def was_already_rotated(self) -> bool:
        """True if this token was consumed by a prior rotation -- presenting it again
        is a replay (docs/05-authentication-flows.md refresh-reuse-detection flow)."""
        return self.revoked_reason == "rotated"

    def rotate(self, *, replacement_id: UUID, now: datetime) -> None:
        if not self.is_active:
            raise InvalidStateTransitionError("cannot rotate an already-revoked refresh token")
        self.rotated_at = now
        self.revoked_at = now
        self.revoked_reason = "rotated"
        self.replaced_by_id = replacement_id

    def revoke(self, *, reason: str, now: datetime) -> None:
        self.revoked_at = now
        self.revoked_reason = reason


@dataclass(kw_only=True)
class EmailVerification(Entity):
    user_id: UUID
    token_hash: str
    purpose: str  # "register" | "email_change"
    new_email: Email | None = None
    expires_at: datetime
    used_at: datetime | None = None
    created_at: datetime
    ip: str | None = None
    user_agent: str | None = None

    def is_valid(self, *, now: datetime) -> bool:
        return self.used_at is None and now < self.expires_at

    def mark_used(self, *, now: datetime) -> None:
        self.used_at = now


@dataclass(kw_only=True)
class AccountLockout(Entity):
    user_id: UUID
    locked_at: datetime
    unlock_at: datetime | None = None
    reason: str
    failed_attempt_count: int
    unlocked_by_user_id: UUID | None = None
    unlocked_at: datetime | None = None

    def is_active(self, *, now: datetime) -> bool:
        return self.unlocked_at is None and (self.unlock_at is None or now < self.unlock_at)

    def unlock(self, *, by_user_id: UUID | None, now: datetime) -> None:
        self.unlocked_at = now
        self.unlocked_by_user_id = by_user_id


@dataclass(kw_only=True)
class PasswordResetToken(Entity):
    user_id: UUID
    token_hash: str
    expires_at: datetime
    used_at: datetime | None = None
    created_at: datetime
    ip: str | None = None
    user_agent: str | None = None

    def is_valid(self, *, now: datetime) -> bool:
        return self.used_at is None and now < self.expires_at

    def mark_used(self, *, now: datetime) -> None:
        self.used_at = now
