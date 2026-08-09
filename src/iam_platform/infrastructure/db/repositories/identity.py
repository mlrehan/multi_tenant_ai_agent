"""SQLAlchemy implementations of the identity-module repository ports.

Domain entities and ORM models are kept deliberately separate (per
docs/20-dependency-rules.md, ``domain`` cannot import SQLAlchemy) -- each
repository method maps explicitly between the two. This is more boilerplate
than a generic ``session.merge()`` approach, but it means an ORM-mapping quirk
can never silently change domain behavior.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from iam_platform.domain.identity.entities import (
    AuthIdentity,
    Credential,
    EmailVerification,
    IdentityKind,
    MfaMethod,
    MfaMethodType,
    OAuthAccount,
    PasswordResetToken,
    RefreshToken,
    User,
    UserStatus,
)
from iam_platform.domain.identity.entities import (
    Session as SessionEntity,
)
from iam_platform.domain.shared.value_objects import Email
from iam_platform.infrastructure.db.models.identity import (
    AuthIdentityModel,
    CredentialModel,
    EmailVerificationModel,
    MfaMethodModel,
    OAuthAccountModel,
    PasswordResetTokenModel,
    RefreshTokenModel,
    SessionModel,
    UserModel,
)


def _user_to_domain(m: UserModel) -> User:
    return User(
        id=m.id,
        email=Email(m.email),
        email_verified_at=m.email_verified_at,
        status=UserStatus(m.status),
        security_stamp=m.security_stamp,
        last_login_at=m.last_login_at,
        created_at=m.created_at,
        updated_at=m.updated_at,
        deleted_at=m.deleted_at,
    )


class SqlUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, user_id: UUID) -> User | None:
        model = await self._session.get(UserModel, user_id)
        return _user_to_domain(model) if model else None

    async def get_by_email(self, email: Email) -> User | None:
        stmt = select(UserModel).where(UserModel.email == str(email))
        model = (await self._session.execute(stmt)).scalar_one_or_none()
        return _user_to_domain(model) if model else None

    async def search(
        self, *, query: str | None, limit: int, offset: int
    ) -> tuple[list[User], int]:
        conditions = [UserModel.deleted_at.is_(None)]
        if query:
            # `ilike` with an escaped pattern -- a user typing '%' in the
            # search box should match a literal percent sign, not every row.
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            conditions.append(UserModel.email.ilike(f"%{escaped}%", escape="\\"))

        total = (
            await self._session.execute(
                select(func.count()).select_from(UserModel).where(*conditions)
            )
        ).scalar_one()

        stmt = (
            select(UserModel)
            .where(*conditions)
            .order_by(UserModel.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        models = (await self._session.execute(stmt)).scalars().all()
        return [_user_to_domain(m) for m in models], total

    async def add(self, user: User) -> None:
        self._session.add(
            UserModel(
                id=user.id,
                email=str(user.email),
                email_verified_at=user.email_verified_at,
                status=user.status.value,
                security_stamp=user.security_stamp,
                last_login_at=user.last_login_at,
                deleted_at=user.deleted_at,
            )
        )
        await self._session.flush()

    async def save(self, user: User) -> None:
        await self._session.execute(
            update(UserModel)
            .where(UserModel.id == user.id)
            .values(
                email=str(user.email),
                email_verified_at=user.email_verified_at,
                status=user.status.value,
                security_stamp=user.security_stamp,
                last_login_at=user.last_login_at,
                deleted_at=user.deleted_at,
            )
        )


def _identity_to_domain(m: AuthIdentityModel) -> AuthIdentity:
    return AuthIdentity(
        id=m.id,
        user_id=m.user_id,
        kind=IdentityKind(m.kind),
        created_at=m.created_at,
        last_used_at=m.last_used_at,
        revoked_at=m.revoked_at,
    )


class SqlAuthIdentityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, identity_id: UUID) -> AuthIdentity | None:
        model = await self._session.get(AuthIdentityModel, identity_id)
        return _identity_to_domain(model) if model else None

    async def get_by_user_and_kind(
        self, user_id: UUID, kind: IdentityKind
    ) -> AuthIdentity | None:
        stmt = select(AuthIdentityModel).where(
            AuthIdentityModel.user_id == user_id, AuthIdentityModel.kind == kind.value
        )
        model = (await self._session.execute(stmt)).scalars().first()
        return _identity_to_domain(model) if model else None

    async def list_by_user(self, user_id: UUID) -> list[AuthIdentity]:
        stmt = select(AuthIdentityModel).where(AuthIdentityModel.user_id == user_id)
        models = (await self._session.execute(stmt)).scalars().all()
        return [_identity_to_domain(m) for m in models]

    async def add(self, identity: AuthIdentity) -> None:
        self._session.add(
            AuthIdentityModel(
                id=identity.id,
                user_id=identity.user_id,
                kind=identity.kind.value,
                last_used_at=identity.last_used_at,
                revoked_at=identity.revoked_at,
            )
        )
        await self._session.flush()

    async def save(self, identity: AuthIdentity) -> None:
        await self._session.execute(
            update(AuthIdentityModel)
            .where(AuthIdentityModel.id == identity.id)
            .values(last_used_at=identity.last_used_at, revoked_at=identity.revoked_at)
        )


def _credential_to_domain(m: CredentialModel) -> Credential:
    return Credential(
        id=m.id,
        identity_id=m.identity_id,
        password_hash=m.password_hash,
        password_algo=m.password_algo,
        password_updated_at=m.password_updated_at,
    )


class SqlCredentialRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_identity_id(self, identity_id: UUID) -> Credential | None:
        stmt = select(CredentialModel).where(CredentialModel.identity_id == identity_id)
        model = (await self._session.execute(stmt)).scalars().first()
        return _credential_to_domain(model) if model else None

    async def add(self, credential: Credential) -> None:
        self._session.add(
            CredentialModel(
                id=credential.id,
                identity_id=credential.identity_id,
                password_hash=credential.password_hash,
                password_algo=credential.password_algo,
                password_updated_at=credential.password_updated_at,
            )
        )
        await self._session.flush()

    async def save(self, credential: Credential) -> None:
        await self._session.execute(
            update(CredentialModel)
            .where(CredentialModel.id == credential.id)
            .values(
                password_hash=credential.password_hash,
                password_algo=credential.password_algo,
                password_updated_at=credential.password_updated_at,
            )
        )


def _oauth_account_to_domain(m: OAuthAccountModel) -> OAuthAccount:
    return OAuthAccount(
        id=m.id,
        identity_id=m.identity_id,
        provider=m.provider,
        provider_subject=m.provider_subject,
        provider_email=m.provider_email,
        raw_profile=m.raw_profile,
        linked_at=m.linked_at,
    )


class SqlOAuthAccountRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_provider_subject(
        self, *, provider: str, subject: str
    ) -> OAuthAccount | None:
        stmt = select(OAuthAccountModel).where(
            OAuthAccountModel.provider == provider, OAuthAccountModel.provider_subject == subject
        )
        model = (await self._session.execute(stmt)).scalars().first()
        return _oauth_account_to_domain(model) if model else None

    async def get_by_identity_id(self, identity_id: UUID) -> OAuthAccount | None:
        stmt = select(OAuthAccountModel).where(OAuthAccountModel.identity_id == identity_id)
        model = (await self._session.execute(stmt)).scalars().first()
        return _oauth_account_to_domain(model) if model else None

    async def add(self, account: OAuthAccount) -> None:
        self._session.add(
            OAuthAccountModel(
                id=account.id,
                identity_id=account.identity_id,
                provider=account.provider,
                provider_subject=account.provider_subject,
                provider_email=account.provider_email,
                raw_profile=account.raw_profile,
                linked_at=account.linked_at,
            )
        )
        await self._session.flush()


def _mfa_to_domain(m: MfaMethodModel) -> MfaMethod:
    return MfaMethod(
        id=m.id,
        user_id=m.user_id,
        type=MfaMethodType(m.type),
        secret_encrypted=m.secret_encrypted,
        webauthn_credential_id=m.webauthn_credential_id,
        webauthn_public_key=m.webauthn_public_key,
        sign_count=m.sign_count,
        label=m.label,
        is_primary=m.is_primary,
        verified_at=m.verified_at,
        created_at=m.created_at,
        last_used_at=m.last_used_at,
        disabled_at=m.disabled_at,
    )


class SqlMfaMethodRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_by_user(self, user_id: UUID) -> list[MfaMethod]:
        stmt = select(MfaMethodModel).where(MfaMethodModel.user_id == user_id)
        models = (await self._session.execute(stmt)).scalars().all()
        return [_mfa_to_domain(m) for m in models]

    async def get_by_id(self, mfa_id: UUID) -> MfaMethod | None:
        model = await self._session.get(MfaMethodModel, mfa_id)
        return _mfa_to_domain(model) if model else None

    async def add(self, method: MfaMethod) -> None:
        self._session.add(
            MfaMethodModel(
                id=method.id,
                user_id=method.user_id,
                type=method.type.value,
                secret_encrypted=method.secret_encrypted,
                webauthn_credential_id=method.webauthn_credential_id,
                webauthn_public_key=method.webauthn_public_key,
                sign_count=method.sign_count,
                label=method.label,
                is_primary=method.is_primary,
                verified_at=method.verified_at,
                last_used_at=method.last_used_at,
                disabled_at=method.disabled_at,
            )
        )
        await self._session.flush()

    async def save(self, method: MfaMethod) -> None:
        await self._session.execute(
            update(MfaMethodModel)
            .where(MfaMethodModel.id == method.id)
            .values(
                sign_count=method.sign_count,
                is_primary=method.is_primary,
                verified_at=method.verified_at,
                last_used_at=method.last_used_at,
                disabled_at=method.disabled_at,
            )
        )


def _session_to_domain(m: SessionModel) -> SessionEntity:
    return SessionEntity(
        id=m.id,
        user_id=m.user_id,
        created_at=m.created_at,
        last_seen_at=m.last_seen_at,
        ip=m.ip,
        user_agent=m.user_agent,
        security_stamp_snapshot=m.security_stamp_snapshot,
        mfa_verified=m.mfa_verified,
        revoked_at=m.revoked_at,
        revoked_reason=m.revoked_reason,
    )


class SqlSessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, session_id: UUID) -> SessionEntity | None:
        model = await self._session.get(SessionModel, session_id)
        return _session_to_domain(model) if model else None

    async def add(self, session: SessionEntity) -> None:
        self._session.add(
            SessionModel(
                id=session.id,
                user_id=session.user_id,
                last_seen_at=session.last_seen_at,
                ip=session.ip,
                user_agent=session.user_agent,
                security_stamp_snapshot=session.security_stamp_snapshot,
                mfa_verified=session.mfa_verified,
                revoked_at=session.revoked_at,
                revoked_reason=session.revoked_reason,
            )
        )
        await self._session.flush()

    async def save(self, session: SessionEntity) -> None:
        await self._session.execute(
            update(SessionModel)
            .where(SessionModel.id == session.id)
            .values(
                last_seen_at=session.last_seen_at,
                revoked_at=session.revoked_at,
                revoked_reason=session.revoked_reason,
            )
        )

    async def revoke_all_for_user(self, user_id: UUID, *, reason: str, now: datetime) -> None:
        await self._session.execute(
            update(SessionModel)
            .where(SessionModel.user_id == user_id, SessionModel.revoked_at.is_(None))
            .values(revoked_at=now, revoked_reason=reason)
        )


def _refresh_token_to_domain(m: RefreshTokenModel) -> RefreshToken:
    return RefreshToken(
        id=m.id,
        user_id=m.user_id,
        session_id=m.session_id,
        family_id=m.family_id,
        token_hash=m.token_hash,
        issued_at=m.issued_at,
        expires_at=m.expires_at,
        rotated_at=m.rotated_at,
        replaced_by_id=m.replaced_by_id,
        revoked_at=m.revoked_at,
        revoked_reason=m.revoked_reason,
    )


class SqlRefreshTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_token_hash(self, token_hash: str) -> RefreshToken | None:
        stmt = select(RefreshTokenModel).where(RefreshTokenModel.token_hash == token_hash)
        model = (await self._session.execute(stmt)).scalars().first()
        return _refresh_token_to_domain(model) if model else None

    async def add(self, token: RefreshToken) -> None:
        self._session.add(
            RefreshTokenModel(
                id=token.id,
                user_id=token.user_id,
                session_id=token.session_id,
                family_id=token.family_id,
                token_hash=token.token_hash,
                issued_at=token.issued_at,
                expires_at=token.expires_at,
                rotated_at=token.rotated_at,
                replaced_by_id=token.replaced_by_id,
                revoked_at=token.revoked_at,
                revoked_reason=token.revoked_reason,
            )
        )
        await self._session.flush()

    async def save(self, token: RefreshToken) -> None:
        await self._session.execute(
            update(RefreshTokenModel)
            .where(RefreshTokenModel.id == token.id)
            .values(
                rotated_at=token.rotated_at,
                replaced_by_id=token.replaced_by_id,
                revoked_at=token.revoked_at,
                revoked_reason=token.revoked_reason,
            )
        )

    async def revoke_family(self, family_id: UUID, *, reason: str, now: datetime) -> None:
        await self._session.execute(
            update(RefreshTokenModel)
            .where(RefreshTokenModel.family_id == family_id, RefreshTokenModel.revoked_at.is_(None))
            .values(revoked_at=now, revoked_reason=reason)
        )

    async def revoke_all_for_user(self, user_id: UUID, *, reason: str, now: datetime) -> None:
        await self._session.execute(
            update(RefreshTokenModel)
            .where(RefreshTokenModel.user_id == user_id, RefreshTokenModel.revoked_at.is_(None))
            .values(revoked_at=now, revoked_reason=reason)
        )


def _email_verification_to_domain(m: EmailVerificationModel) -> EmailVerification:
    return EmailVerification(
        id=m.id,
        user_id=m.user_id,
        token_hash=m.token_hash,
        purpose=m.purpose,
        new_email=Email(m.new_email) if m.new_email else None,
        expires_at=m.expires_at,
        used_at=m.used_at,
        created_at=m.created_at,
        ip=m.ip,
        user_agent=m.user_agent,
    )


class SqlEmailVerificationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_token_hash(self, token_hash: str) -> EmailVerification | None:
        stmt = select(EmailVerificationModel).where(
            EmailVerificationModel.token_hash == token_hash
        )
        model = (await self._session.execute(stmt)).scalars().first()
        return _email_verification_to_domain(model) if model else None

    async def add(self, verification: EmailVerification) -> None:
        self._session.add(
            EmailVerificationModel(
                id=verification.id,
                user_id=verification.user_id,
                token_hash=verification.token_hash,
                purpose=verification.purpose,
                new_email=str(verification.new_email) if verification.new_email else None,
                expires_at=verification.expires_at,
                used_at=verification.used_at,
                ip=verification.ip,
                user_agent=verification.user_agent,
            )
        )
        await self._session.flush()

    async def save(self, verification: EmailVerification) -> None:
        await self._session.execute(
            update(EmailVerificationModel)
            .where(EmailVerificationModel.id == verification.id)
            .values(used_at=verification.used_at)
        )


def _password_reset_token_to_domain(m: PasswordResetTokenModel) -> PasswordResetToken:
    return PasswordResetToken(
        id=m.id,
        user_id=m.user_id,
        token_hash=m.token_hash,
        expires_at=m.expires_at,
        used_at=m.used_at,
        created_at=m.created_at,
        ip=m.ip,
        user_agent=m.user_agent,
    )


class SqlPasswordResetTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_token_hash(self, token_hash: str) -> PasswordResetToken | None:
        stmt = select(PasswordResetTokenModel).where(
            PasswordResetTokenModel.token_hash == token_hash
        )
        model = (await self._session.execute(stmt)).scalars().first()
        return _password_reset_token_to_domain(model) if model else None

    async def add(self, token: PasswordResetToken) -> None:
        self._session.add(
            PasswordResetTokenModel(
                id=token.id,
                user_id=token.user_id,
                token_hash=token.token_hash,
                expires_at=token.expires_at,
                used_at=token.used_at,
                ip=token.ip,
                user_agent=token.user_agent,
            )
        )
        await self._session.flush()

    async def save(self, token: PasswordResetToken) -> None:
        await self._session.execute(
            update(PasswordResetTokenModel)
            .where(PasswordResetTokenModel.id == token.id)
            .values(used_at=token.used_at)
        )
