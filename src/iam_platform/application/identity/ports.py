"""Ports (Protocols) the identity use cases depend on.

Implemented by ``infrastructure`` (repositories, security adapters, cache,
OAuth clients). Defined here rather than as ABCs so infrastructure classes
satisfy them structurally, with no inheritance coupling -- see
docs/20-dependency-rules.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from types import TracebackType
from typing import Any, Protocol
from uuid import UUID

from iam_platform.domain.identity.entities import (
    AccountLockout,
    AuthIdentity,
    Credential,
    EmailVerification,
    IdentityKind,
    MfaMethod,
    OAuthAccount,
    PasswordResetToken,
    RefreshToken,
    Session,
    User,
)
from iam_platform.domain.shared.value_objects import Email

# --- Repositories -----------------------------------------------------------


class UserRepository(Protocol):
    async def get_by_id(self, user_id: UUID) -> User | None: ...
    async def get_by_email(self, email: Email) -> User | None: ...
    async def add(self, user: User) -> None: ...
    async def save(self, user: User) -> None: ...

    async def search(
        self, *, query: str | None, limit: int, offset: int
    ) -> tuple[list[User], int]:
        """Platform-scope user directory: a page of users plus the total match
        count. Returns non-deleted users only, newest first. `query` matches
        the email substring, case-insensitively.

        Paginated at the port rather than filtered in the caller because the
        platform user table is the one table in this system explicitly sized
        for millions of rows (CLAUDE.md) -- a `list_all()` here would be a
        latent outage."""
        ...


class AuthIdentityRepository(Protocol):
    async def get_by_id(self, identity_id: UUID) -> AuthIdentity | None: ...
    async def get_by_user_and_kind(
        self, user_id: UUID, kind: IdentityKind
    ) -> AuthIdentity | None: ...
    async def list_by_user(self, user_id: UUID) -> list[AuthIdentity]: ...
    async def add(self, identity: AuthIdentity) -> None: ...
    async def save(self, identity: AuthIdentity) -> None: ...


class CredentialRepository(Protocol):
    async def get_by_identity_id(self, identity_id: UUID) -> Credential | None: ...
    async def add(self, credential: Credential) -> None: ...
    async def save(self, credential: Credential) -> None: ...


class OAuthAccountRepository(Protocol):
    async def get_by_provider_subject(
        self, *, provider: str, subject: str
    ) -> OAuthAccount | None: ...
    async def get_by_identity_id(self, identity_id: UUID) -> OAuthAccount | None: ...
    async def add(self, account: OAuthAccount) -> None: ...


class MfaMethodRepository(Protocol):
    async def list_by_user(self, user_id: UUID) -> list[MfaMethod]: ...
    async def get_by_id(self, mfa_id: UUID) -> MfaMethod | None: ...
    async def add(self, method: MfaMethod) -> None: ...
    async def save(self, method: MfaMethod) -> None: ...


class SessionRepository(Protocol):
    async def get_by_id(self, session_id: UUID) -> Session | None: ...
    async def add(self, session: Session) -> None: ...
    async def save(self, session: Session) -> None: ...
    async def revoke_all_for_user(self, user_id: UUID, *, reason: str, now: datetime) -> None: ...


class RefreshTokenRepository(Protocol):
    async def get_by_token_hash(self, token_hash: str) -> RefreshToken | None: ...
    async def add(self, token: RefreshToken) -> None: ...
    async def save(self, token: RefreshToken) -> None: ...
    async def revoke_family(self, family_id: UUID, *, reason: str, now: datetime) -> None: ...
    async def revoke_all_for_user(self, user_id: UUID, *, reason: str, now: datetime) -> None: ...


class EmailVerificationRepository(Protocol):
    async def get_by_token_hash(self, token_hash: str) -> EmailVerification | None: ...
    async def add(self, verification: EmailVerification) -> None: ...
    async def save(self, verification: EmailVerification) -> None: ...


class PasswordResetTokenRepository(Protocol):
    async def get_by_token_hash(self, token_hash: str) -> PasswordResetToken | None: ...
    async def add(self, token: PasswordResetToken) -> None: ...
    async def save(self, token: PasswordResetToken) -> None: ...


class LoginAttemptRepository(Protocol):
    async def record(
        self,
        *,
        email_attempted: str,
        user_id: UUID | None,
        result: str,
        ip: str | None,
        user_agent: str | None,
        now: datetime,
    ) -> None: ...

    async def count_recent_failures(self, *, email: str, since: datetime) -> int: ...


class AccountLockoutRepository(Protocol):
    async def get_active(self, *, user_id: UUID, now: datetime) -> AccountLockout | None: ...
    async def add(self, lockout: AccountLockout) -> None: ...


class SecurityEventWriter(Protocol):
    async def record(
        self,
        *,
        user_id: UUID | None,
        tenant_id: UUID | None,
        event_type: str,
        severity: str,
        details: dict[str, Any] | None = None,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> None: ...


class IdentityUnitOfWork(Protocol):
    """One transaction's worth of identity-module repositories, per
    docs/18-schema-rls-and-migrations.md's session-per-request pattern.
    ``__aenter__`` opens the transaction (and, for real Postgres, issues the
    ``SET LOCAL`` context); ``__aexit__`` commits on success / rolls back on
    exception."""

    users: UserRepository
    identities: AuthIdentityRepository
    credentials: CredentialRepository
    oauth_accounts: OAuthAccountRepository
    mfa_methods: MfaMethodRepository
    sessions: SessionRepository
    refresh_tokens: RefreshTokenRepository
    email_verifications: EmailVerificationRepository
    password_reset_tokens: PasswordResetTokenRepository
    login_attempts: LoginAttemptRepository
    account_lockouts: AccountLockoutRepository
    audit: AuditWriter
    security_events: SecurityEventWriter

    async def __aenter__(self) -> IdentityUnitOfWork: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...


class AuditWriter(Protocol):
    async def record(
        self,
        *,
        actor_user_id: UUID | None,
        effective_user_id: UUID | None,
        tenant_id: UUID | None,
        action: str,
        resource_type: str | None = None,
        resource_id: UUID | None = None,
        result: str,
        failure_reason: str | None = None,
        ip: str | None = None,
        user_agent: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None: ...


# --- Security services -------------------------------------------------------


class PasswordHasher(Protocol):
    def hash(self, password: str) -> str: ...
    def verify(self, password: str, hashed: str) -> bool: ...
    def needs_rehash(self, hashed: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class IssuedAccessToken:
    token: str
    token_id: UUID
    expires_at: datetime


class JwtIssuer(Protocol):
    def issue_access_token(
        self,
        *,
        user_id: UUID,
        session_id: UUID,
        amr: list[str],
        auth_time: datetime,
        now: datetime,
        actor: dict[str, Any] | None = None,
    ) -> IssuedAccessToken: ...


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    user_id: UUID
    session_id: UUID
    token_id: UUID
    amr: list[str]
    auth_time: datetime
    actor: dict[str, Any] | None = None


class JwtVerifier(Protocol):
    def verify(self, token: str) -> AccessTokenClaims: ...


class TotpService(Protocol):
    def generate_secret(self) -> str: ...
    def provisioning_uri(self, *, secret: str, account_email: str) -> str: ...
    def verify(self, *, secret: str, code: str) -> bool: ...


class MfaChallengeStore(Protocol):
    """Short-TTL server-side pending-MFA state, keyed by an opaque challenge id
    (docs/05-authentication-flows.md login+MFA step-up flow)."""

    async def create_challenge(self, *, user_id: UUID, now: datetime) -> str: ...
    async def get_user_id(self, challenge_id: str) -> UUID | None: ...
    async def consume(self, challenge_id: str) -> None: ...


class OAuthStateStore(Protocol):
    """Server-side state/nonce/PKCE-verifier storage for the OAuth authorization-code flow."""

    async def create(self, *, provider: str, now: datetime) -> tuple[str, str, str, str]:
        """Returns (state, nonce, code_verifier, code_challenge)."""
        ...

    async def consume(self, *, state: str) -> tuple[str, str, str] | None:
        """Returns (provider, nonce, code_verifier) if state is valid and unused, else None."""
        ...


class RateLimiter(Protocol):
    async def check_and_increment(self, key: str, *, limit: int, window_seconds: int) -> bool:
        """Returns True if the action is allowed, False if the limit was exceeded."""
        ...


class EmailSender(Protocol):
    async def send_verification_email(self, *, to: str, token: str) -> None: ...
    async def send_password_reset_email(self, *, to: str, token: str) -> None: ...


@dataclass(frozen=True, slots=True)
class OAuthProfile:
    provider: str
    subject: str
    email: str | None
    raw_profile: dict[str, Any] = field(default_factory=dict)


class OAuthProvider(Protocol):
    """One instance per provider (google, facebook, ...). The state/nonce/PKCE
    challenge itself is verified inside ``exchange_code`` -- callers only ever
    see an already-verified profile or an exception."""

    provider_name: str

    def build_authorization_url(self, *, state: str, nonce: str, code_challenge: str) -> str: ...

    async def exchange_code(
        self, *, code: str, code_verifier: str, expected_nonce: str
    ) -> OAuthProfile: ...
