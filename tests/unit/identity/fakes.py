"""In-memory fakes for every identity port -- used by unit tests so the use
cases under test never touch a real database, Redis, or crypto library.
"""

from __future__ import annotations

import copy
from datetime import datetime
from uuid import UUID, uuid4

from iam_platform.application.identity.ports import (
    AccountLockoutRepository,
    AuthIdentityRepository,
    CredentialRepository,
    EmailSender,
    EmailVerificationRepository,
    IssuedAccessToken,
    JwtIssuer,
    LoginAttemptRepository,
    MfaChallengeStore,
    MfaMethodRepository,
    OAuthAccountRepository,
    OAuthProfile,
    OAuthProvider,
    PasswordHasher,
    PasswordResetTokenRepository,
    RateLimiter,
    RefreshTokenRepository,
    SecurityEventWriter,
    SessionRepository,
    TotpService,
    UserRepository,
)
from iam_platform.domain.identity.entities import (
    AccountLockout,
    AuthIdentity,
    Credential,
    EmailVerification,
    MfaMethod,
    OAuthAccount,
    PasswordResetToken,
    RefreshToken,
    Session,
    User,
)
from iam_platform.domain.shared.value_objects import Email


class FakeUserRepository(UserRepository):
    def __init__(self) -> None:
        self.by_id: dict[UUID, User] = {}

    async def get_by_id(self, user_id: UUID) -> User | None:
        return self.by_id.get(user_id)

    async def get_by_email(self, email: Email) -> User | None:
        return next((u for u in self.by_id.values() if u.email == email), None)

    async def add(self, user: User) -> None:
        self.by_id[user.id] = user

    async def save(self, user: User) -> None:
        self.by_id[user.id] = user


class FakeAuthIdentityRepository(AuthIdentityRepository):
    def __init__(self) -> None:
        self.by_id: dict[UUID, AuthIdentity] = {}

    async def get_by_id(self, identity_id: UUID) -> AuthIdentity | None:
        return self.by_id.get(identity_id)

    async def get_by_user_and_kind(self, user_id: UUID, kind: object) -> AuthIdentity | None:
        return next(
            (i for i in self.by_id.values() if i.user_id == user_id and i.kind == kind), None
        )

    async def list_by_user(self, user_id: UUID) -> list[AuthIdentity]:
        return [i for i in self.by_id.values() if i.user_id == user_id]

    async def add(self, identity: AuthIdentity) -> None:
        self.by_id[identity.id] = identity

    async def save(self, identity: AuthIdentity) -> None:
        self.by_id[identity.id] = identity


class FakeCredentialRepository(CredentialRepository):
    def __init__(self) -> None:
        self.by_identity_id: dict[UUID, Credential] = {}

    async def get_by_identity_id(self, identity_id: UUID) -> Credential | None:
        return self.by_identity_id.get(identity_id)

    async def add(self, credential: Credential) -> None:
        self.by_identity_id[credential.identity_id] = credential

    async def save(self, credential: Credential) -> None:
        self.by_identity_id[credential.identity_id] = credential


class FakeOAuthAccountRepository(OAuthAccountRepository):
    def __init__(self) -> None:
        self.by_id: dict[UUID, OAuthAccount] = {}

    async def get_by_provider_subject(self, *, provider: str, subject: str) -> OAuthAccount | None:
        return next(
            (
                a
                for a in self.by_id.values()
                if a.provider == provider and a.provider_subject == subject
            ),
            None,
        )

    async def get_by_identity_id(self, identity_id: UUID) -> OAuthAccount | None:
        return next((a for a in self.by_id.values() if a.identity_id == identity_id), None)

    async def add(self, account: OAuthAccount) -> None:
        self.by_id[account.id] = account


class FakeMfaMethodRepository(MfaMethodRepository):
    def __init__(self) -> None:
        self.by_id: dict[UUID, MfaMethod] = {}

    async def list_by_user(self, user_id: UUID) -> list[MfaMethod]:
        return [m for m in self.by_id.values() if m.user_id == user_id]

    async def get_by_id(self, mfa_id: UUID) -> MfaMethod | None:
        return self.by_id.get(mfa_id)

    async def add(self, method: MfaMethod) -> None:
        self.by_id[method.id] = method

    async def save(self, method: MfaMethod) -> None:
        self.by_id[method.id] = method


class FakeSessionRepository(SessionRepository):
    def __init__(self) -> None:
        self.by_id: dict[UUID, Session] = {}

    async def get_by_id(self, session_id: UUID) -> Session | None:
        return self.by_id.get(session_id)

    async def add(self, session: Session) -> None:
        self.by_id[session.id] = session

    async def save(self, session: Session) -> None:
        self.by_id[session.id] = session

    async def revoke_all_for_user(self, user_id: UUID, *, reason: str, now: datetime) -> None:
        for s in self.by_id.values():
            if s.user_id == user_id and s.revoked_at is None:
                s.revoke(reason=reason, now=now)


class FakeRefreshTokenRepository(RefreshTokenRepository):
    def __init__(self) -> None:
        self.by_id: dict[UUID, RefreshToken] = {}

    async def get_by_token_hash(self, token_hash: str) -> RefreshToken | None:
        return next((t for t in self.by_id.values() if t.token_hash == token_hash), None)

    async def add(self, token: RefreshToken) -> None:
        self.by_id[token.id] = token

    async def save(self, token: RefreshToken) -> None:
        self.by_id[token.id] = token

    async def revoke_family(self, family_id: UUID, *, reason: str, now: datetime) -> None:
        for t in self.by_id.values():
            if t.family_id == family_id and t.revoked_at is None:
                t.revoke(reason=reason, now=now)

    async def revoke_all_for_user(self, user_id: UUID, *, reason: str, now: datetime) -> None:
        for t in self.by_id.values():
            if t.user_id == user_id and t.revoked_at is None:
                t.revoke(reason=reason, now=now)


class FakeEmailVerificationRepository(EmailVerificationRepository):
    def __init__(self) -> None:
        self.by_id: dict[UUID, EmailVerification] = {}

    async def get_by_token_hash(self, token_hash: str) -> EmailVerification | None:
        return next((v for v in self.by_id.values() if v.token_hash == token_hash), None)

    async def add(self, verification: EmailVerification) -> None:
        self.by_id[verification.id] = verification

    async def save(self, verification: EmailVerification) -> None:
        self.by_id[verification.id] = verification


class FakePasswordResetTokenRepository(PasswordResetTokenRepository):
    def __init__(self) -> None:
        self.by_id: dict[UUID, PasswordResetToken] = {}

    async def get_by_token_hash(self, token_hash: str) -> PasswordResetToken | None:
        return next((t for t in self.by_id.values() if t.token_hash == token_hash), None)

    async def add(self, token: PasswordResetToken) -> None:
        self.by_id[token.id] = token

    async def save(self, token: PasswordResetToken) -> None:
        self.by_id[token.id] = token


class FakeLoginAttemptRepository(LoginAttemptRepository):
    def __init__(self) -> None:
        self.records: list[dict] = []

    async def record(
        self,
        *,
        email_attempted: str,
        user_id: UUID | None,
        result: str,
        ip: str | None,
        user_agent: str | None,
        now: datetime,
    ) -> None:
        self.records.append(
            {"email": email_attempted, "user_id": user_id, "result": result, "at": now}
        )

    async def count_recent_failures(self, *, email: str, since: datetime) -> int:
        return sum(
            1
            for r in self.records
            if r["email"].lower() == email.lower() and r["result"] != "success" and r["at"] >= since
        )


class FakeAccountLockoutRepository(AccountLockoutRepository):
    def __init__(self) -> None:
        self.by_id: dict[UUID, AccountLockout] = {}

    async def get_active(self, *, user_id: UUID, now: datetime) -> AccountLockout | None:
        return next(
            (
                lockout
                for lockout in self.by_id.values()
                if lockout.user_id == user_id and lockout.is_active(now=now)
            ),
            None,
        )

    async def add(self, lockout: AccountLockout) -> None:
        self.by_id[lockout.id] = lockout


class FakeAuditWriter:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def record(self, **kwargs: object) -> None:
        self.events.append(kwargs)


class FakeSecurityEventWriter(SecurityEventWriter):
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def record(self, **kwargs: object) -> None:
        self.events.append(kwargs)


class FakeIdentityUnitOfWork:
    """A single shared instance acts as both the UoW *and* the factory
    (``self()`` returns itself) so tests can inspect state after the use
    case under test has run its ``async with`` block.

    Simulates real transaction rollback-on-exception (every repo attribute is
    snapshotted on ``__aenter__`` and restored if the block raises) --
    deliberately, after a real bug (docs: refresh_session.py / login_user.py
    / mfa.py commit history) where writes made right before an intentionally
    raised application exception were silently rolled back by the real
    ``SqlUnitOfWork`` but NOT by an earlier, no-op version of this fake,
    letting the bug pass unit tests while still being wrong against a real
    database. A fake with no rollback semantics is a worse test double than
    one that matches the real transactional behavior it stands in for.
    """

    _REPO_ATTRS = (
        "users",
        "identities",
        "credentials",
        "oauth_accounts",
        "mfa_methods",
        "sessions",
        "refresh_tokens",
        "email_verifications",
        "password_reset_tokens",
        "login_attempts",
        "account_lockouts",
        "audit",
        "security_events",
    )

    def __init__(self) -> None:
        self.users = FakeUserRepository()
        self.identities = FakeAuthIdentityRepository()
        self.credentials = FakeCredentialRepository()
        self.oauth_accounts = FakeOAuthAccountRepository()
        self.mfa_methods = FakeMfaMethodRepository()
        self.sessions = FakeSessionRepository()
        self.refresh_tokens = FakeRefreshTokenRepository()
        self.email_verifications = FakeEmailVerificationRepository()
        self.password_reset_tokens = FakePasswordResetTokenRepository()
        self.login_attempts = FakeLoginAttemptRepository()
        self.account_lockouts = FakeAccountLockoutRepository()
        self.audit = FakeAuditWriter()
        self.security_events = FakeSecurityEventWriter()
        self._snapshot: dict[str, object] | None = None

    def __call__(self) -> FakeIdentityUnitOfWork:
        return self

    async def __aenter__(self) -> FakeIdentityUnitOfWork:
        self._snapshot = {name: copy.deepcopy(getattr(self, name)) for name in self._REPO_ATTRS}
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        if exc_type is not None:
            assert self._snapshot is not None
            for name, value in self._snapshot.items():
                setattr(self, name, value)
        self._snapshot = None


class FakePasswordHasher(PasswordHasher):
    """Not real hashing -- a fast, deterministic stand-in so unit tests don't
    pay Argon2id's (deliberately expensive) cost on every run."""

    def hash(self, password: str) -> str:
        return f"hashed:{password}"

    def verify(self, password: str, hashed: str) -> bool:
        return hashed == f"hashed:{password}"

    def needs_rehash(self, hashed: str) -> bool:
        return False


class FakeJwtIssuer(JwtIssuer):
    def issue_access_token(
        self,
        *,
        user_id: UUID,
        session_id: UUID,
        amr: list[str],
        auth_time: datetime,
        now: datetime,
        actor: dict | None = None,
    ) -> IssuedAccessToken:
        return IssuedAccessToken(token=f"jwt-for-{user_id}", token_id=uuid4(), expires_at=now)


class FakeTotpService(TotpService):
    """Codes are just the secret itself, reversed -- deterministic and
    trivially fake-able without depending on wall-clock time windows."""

    def generate_secret(self) -> str:
        return "FAKESECRET"

    def provisioning_uri(self, *, secret: str, account_email: str) -> str:
        return f"otpauth://totp/{account_email}?secret={secret}"

    def verify(self, *, secret: str, code: str) -> bool:
        return code == secret[::-1]


class FakeMfaChallengeStore(MfaChallengeStore):
    def __init__(self) -> None:
        self._by_id: dict[str, UUID] = {}

    async def create_challenge(self, *, user_id: UUID, now: datetime) -> str:
        challenge_id = f"challenge-{uuid4()}"
        self._by_id[challenge_id] = user_id
        return challenge_id

    async def get_user_id(self, challenge_id: str) -> UUID | None:
        return self._by_id.get(challenge_id)

    async def consume(self, challenge_id: str) -> None:
        self._by_id.pop(challenge_id, None)


class FakeRateLimiter(RateLimiter):
    def __init__(self, *, always_allow: bool = True) -> None:
        self._always_allow = always_allow

    async def check_and_increment(self, key: str, *, limit: int, window_seconds: int) -> bool:
        return self._always_allow


class FakeEmailSender(EmailSender):
    def __init__(self) -> None:
        self.verification_emails: list[tuple[str, str]] = []
        self.reset_emails: list[tuple[str, str]] = []

    async def send_verification_email(self, *, to: str, token: str) -> None:
        self.verification_emails.append((to, token))

    async def send_password_reset_email(self, *, to: str, token: str) -> None:
        self.reset_emails.append((to, token))


class FakeOAuthProvider(OAuthProvider):
    provider_name = "fake"

    def __init__(self, profile: OAuthProfile) -> None:
        self._profile = profile

    def build_authorization_url(self, *, state: str, nonce: str, code_challenge: str) -> str:
        return f"https://fake-provider.invalid/authorize?state={state}"

    async def exchange_code(
        self, *, code: str, code_verifier: str, expected_nonce: str
    ) -> OAuthProfile:
        return self._profile
