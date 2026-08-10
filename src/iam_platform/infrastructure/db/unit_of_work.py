"""Concrete Unit of Work implementations -- one transaction, repositories bound to its session.

Identity-domain tables are not tenant-owned (docs/11-schema-global-identity.md),
so ``SqlIdentityUnitOfWork`` never issues the RLS context-setting calls that
``SqlTenantUnitOfWork``/``SqlPlatformUnitOfWork`` (below) do -- see
docs/18-schema-rls-and-migrations.md, including the Phase 6 correction on
using ``set_config(name, value, true)`` (parameterized) rather than literal
``SET LOCAL`` text, and the ``NULLIF(..., '')`` cast guard on the read side.
"""

from __future__ import annotations

from collections.abc import Callable
from types import TracebackType
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from iam_platform.application.ai_resources.ports import (
    AiAssistantRepository,
    AssistantMemberRepository,
    ChatWidgetRepository,
    ConversationRepository,
    DataSourceRepository,
    DocumentRepository,
    KnowledgeBaseRepository,
    ModelConfigurationRepository,
    ProviderCredentialRepository,
)
from iam_platform.application.identity.ports import (
    AccountLockoutRepository,
    AuditWriter,
    AuthIdentityRepository,
    CredentialRepository,
    EmailVerificationRepository,
    LoginAttemptRepository,
    MfaMethodRepository,
    OAuthAccountRepository,
    PasswordResetTokenRepository,
    RefreshTokenRepository,
    SecurityEventWriter,
    SessionRepository,
    UserRepository,
)
from iam_platform.application.platform_authz.ports import (
    ImpersonationSessionRepository,
    PlatformModelConfigurationRepository,
    PlatformPermissionRepository,
    PlatformRoleRepository,
    PlatformUserRoleRepository,
    TenantModelAccessRepository,
)
from iam_platform.application.tenancy.ports import (
    TenantFeatureRepository,
    TenantInvitationRepository,
    TenantMembershipRepository,
    TenantRepository,
)
from iam_platform.application.tenant_authz.ports import (
    AuthorizationOverrideRepository,
    RoleHierarchyRepository,
    TenantMembershipRoleRepository,
    TenantPermissionRepository,
    TenantRoleRepository,
)
from iam_platform.infrastructure.db.repositories.ai_resources import (
    SqlAiAssistantRepository,
    SqlAssistantMemberRepository,
    SqlChatWidgetRepository,
    SqlConversationRepository,
    SqlDataSourceRepository,
    SqlDocumentRepository,
    SqlKnowledgeBaseRepository,
    SqlModelConfigurationRepository,
    SqlPlatformModelConfigurationRepository,
    SqlProviderCredentialRepository,
    SqlTenantModelAccessRepository,
)
from iam_platform.infrastructure.db.repositories.audit import (
    SqlAccountLockoutRepository,
    SqlAuditWriter,
    SqlLoginAttemptRepository,
    SqlSecurityEventWriter,
)
from iam_platform.infrastructure.db.repositories.identity import (
    SqlAuthIdentityRepository,
    SqlCredentialRepository,
    SqlEmailVerificationRepository,
    SqlMfaMethodRepository,
    SqlOAuthAccountRepository,
    SqlPasswordResetTokenRepository,
    SqlRefreshTokenRepository,
    SqlSessionRepository,
    SqlUserRepository,
)
from iam_platform.infrastructure.db.repositories.platform_authz import (
    SqlImpersonationSessionRepository,
    SqlPlatformPermissionRepository,
    SqlPlatformRoleRepository,
    SqlPlatformUserRoleRepository,
)
from iam_platform.infrastructure.db.repositories.tenancy import (
    SqlTenantFeatureRepository,
    SqlTenantInvitationRepository,
    SqlTenantMembershipRepository,
    SqlTenantRepository,
)
from iam_platform.infrastructure.db.repositories.tenant_authz import (
    SqlAuthorizationOverrideRepository,
    SqlRoleHierarchyRepository,
    SqlTenantMembershipRoleRepository,
    SqlTenantPermissionRepository,
    SqlTenantRoleRepository,
)


class SqlIdentityUnitOfWork:
    # Declared here (types only -- real objects are constructed in
    # __aenter__, once a session exists) so mypy can verify this class
    # structurally satisfies application.identity.ports.IdentityUnitOfWork.
    # Typed as the *port* (Protocol), not the concrete Sql* class: mypy treats
    # a plain mutable attribute on a Protocol as invariant, so declaring it
    # here as e.g. `SqlAccountLockoutRepository` (a subtype of
    # `AccountLockoutRepository`) would fail Protocol conformance even though
    # the concrete class structurally implements every method the port
    # requires -- the attribute's declared type is what has to match exactly.
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

    def __init__(self, session_factory: Callable[[], AsyncSession]) -> None:
        self._session_factory = session_factory
        self.session: AsyncSession | None = None

    async def __aenter__(self) -> SqlIdentityUnitOfWork:
        self.session = self._session_factory()
        session = self.session

        self.users = SqlUserRepository(session)
        self.identities = SqlAuthIdentityRepository(session)
        self.credentials = SqlCredentialRepository(session)
        self.oauth_accounts = SqlOAuthAccountRepository(session)
        self.mfa_methods = SqlMfaMethodRepository(session)
        self.sessions = SqlSessionRepository(session)
        self.refresh_tokens = SqlRefreshTokenRepository(session)
        self.email_verifications = SqlEmailVerificationRepository(session)
        self.password_reset_tokens = SqlPasswordResetTokenRepository(session)
        self.login_attempts = SqlLoginAttemptRepository(session)
        self.account_lockouts = SqlAccountLockoutRepository(session)
        self.audit = SqlAuditWriter(session)
        self.security_events = SqlSecurityEventWriter(session)

        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        assert self.session is not None
        try:
            if exc_type is None:
                await self.session.commit()
            else:
                await self.session.rollback()
        finally:
            await self.session.close()


class SqlTenantUnitOfWork:
    """Opened with a verified ``user_id`` and (usually) a verified
    ``tenant_id``. ``tenant_id=None`` opens a self-lookup-only transaction --
    see docs/18-schema-rls-and-migrations.md's self-lookup exception and
    ``application/tenancy/list_memberships.py``.
    """

    tenant_memberships: TenantMembershipRepository
    tenant_invitations: TenantInvitationRepository
    tenant_features: TenantFeatureRepository
    tenant_roles: TenantRoleRepository
    tenant_permissions: TenantPermissionRepository
    tenant_membership_roles: TenantMembershipRoleRepository
    role_hierarchy: RoleHierarchyRepository
    authorization_overrides: AuthorizationOverrideRepository
    audit: AuditWriter
    security_events: SecurityEventWriter

    def __init__(
        self,
        session_factory: Callable[[], AsyncSession],
        *,
        user_id: UUID,
        tenant_id: UUID | None,
    ) -> None:
        self._session_factory = session_factory
        self._user_id = user_id
        self._tenant_id = tenant_id
        self.session: AsyncSession | None = None

    async def __aenter__(self) -> SqlTenantUnitOfWork:
        self.session = self._session_factory()
        session = self.session

        # set_config(..., true) is the parameterized equivalent of SET LOCAL
        # -- see the Phase 6 pitfall note in docs/18-schema-rls-and-migrations.md
        # for why literal SET LOCAL text and a plain ::uuid cast on the read
        # side are both wrong under connection pooling.
        await session.execute(
            text("SELECT set_config('app.user_id', :uid, true)"), {"uid": str(self._user_id)}
        )
        if self._tenant_id is not None:
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tid, true)"),
                {"tid": str(self._tenant_id)},
            )

        self.tenant_memberships = SqlTenantMembershipRepository(session)
        self.tenant_invitations = SqlTenantInvitationRepository(session)
        self.tenant_features = SqlTenantFeatureRepository(session)
        self.tenant_roles = SqlTenantRoleRepository(session)
        self.tenant_permissions = SqlTenantPermissionRepository(session)
        self.tenant_membership_roles = SqlTenantMembershipRoleRepository(session)
        self.role_hierarchy = SqlRoleHierarchyRepository(session)
        self.authorization_overrides = SqlAuthorizationOverrideRepository(session)
        self.audit = SqlAuditWriter(session)
        self.security_events = SqlSecurityEventWriter(session)

        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        assert self.session is not None
        try:
            if exc_type is None:
                await self.session.commit()
            else:
                await self.session.rollback()
        finally:
            await self.session.close()


class SqlAiResourceUnitOfWork:
    """RLS-subject (``app_tenant``) boundary for AI-resource operations.

    Unlike ``SqlTenantUnitOfWork`` the ``tenant_id`` is required: every AI
    resource is tenant-owned, so there is no self-lookup bootstrap state to
    support here.
    """

    tenant_memberships: TenantMembershipRepository
    assistants: AiAssistantRepository
    assistant_members: AssistantMemberRepository
    knowledge_bases: KnowledgeBaseRepository
    documents: DocumentRepository
    data_sources: DataSourceRepository
    chat_widgets: ChatWidgetRepository
    conversations: ConversationRepository
    model_configurations: ModelConfigurationRepository
    provider_credentials: ProviderCredentialRepository
    audit: AuditWriter
    security_events: SecurityEventWriter

    def __init__(
        self,
        session_factory: Callable[[], AsyncSession],
        *,
        user_id: UUID,
        tenant_id: UUID,
    ) -> None:
        self._session_factory = session_factory
        self._user_id = user_id
        self._tenant_id = tenant_id
        self.session: AsyncSession | None = None

    async def __aenter__(self) -> SqlAiResourceUnitOfWork:
        self.session = self._session_factory()
        session = self.session

        await session.execute(
            text("SELECT set_config('app.user_id', :uid, true)"), {"uid": str(self._user_id)}
        )
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": str(self._tenant_id)}
        )

        self.tenant_memberships = SqlTenantMembershipRepository(session)
        self.assistants = SqlAiAssistantRepository(session)
        self.assistant_members = SqlAssistantMemberRepository(session)
        self.knowledge_bases = SqlKnowledgeBaseRepository(session)
        self.documents = SqlDocumentRepository(session)
        self.data_sources = SqlDataSourceRepository(session)
        self.chat_widgets = SqlChatWidgetRepository(session)
        self.conversations = SqlConversationRepository(session)
        self.model_configurations = SqlModelConfigurationRepository(session)
        self.provider_credentials = SqlProviderCredentialRepository(session)
        self.audit = SqlAuditWriter(session)
        self.security_events = SqlSecurityEventWriter(session)

        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        assert self.session is not None
        try:
            if exc_type is None:
                await self.session.commit()
            else:
                await self.session.rollback()
        finally:
            await self.session.close()


class SqlPlatformUnitOfWork:
    """BYPASSRLS (``app_platform``) -- used only by the small, reviewed set of
    platform-scope application code (docs/20-dependency-rules.md).
    """

    platform_roles: PlatformRoleRepository
    platform_permissions: PlatformPermissionRepository
    platform_user_roles: PlatformUserRoleRepository
    users: UserRepository
    identities: AuthIdentityRepository
    credentials: CredentialRepository
    sessions: SessionRepository
    refresh_tokens: RefreshTokenRepository
    tenants: TenantRepository
    tenant_memberships: TenantMembershipRepository
    tenant_roles: TenantRoleRepository
    tenant_membership_roles: TenantMembershipRoleRepository
    role_hierarchy: RoleHierarchyRepository
    authorization_overrides: AuthorizationOverrideRepository
    impersonation_sessions: ImpersonationSessionRepository
    model_configurations: PlatformModelConfigurationRepository
    tenant_model_access: TenantModelAccessRepository
    audit: AuditWriter
    security_events: SecurityEventWriter

    def __init__(self, session_factory: Callable[[], AsyncSession], *, user_id: UUID) -> None:
        self._session_factory = session_factory
        self._user_id = user_id
        self.session: AsyncSession | None = None

    async def __aenter__(self) -> SqlPlatformUnitOfWork:
        self.session = self._session_factory()
        session = self.session

        # BYPASSRLS makes this a no-op for row visibility, but app.user_id is
        # still set for consistency (and for any future DB-level audit
        # trigger that might reference it).
        await session.execute(
            text("SELECT set_config('app.user_id', :uid, true)"), {"uid": str(self._user_id)}
        )

        self.platform_roles = SqlPlatformRoleRepository(session)
        self.platform_permissions = SqlPlatformPermissionRepository(session)
        self.platform_user_roles = SqlPlatformUserRoleRepository(session)
        self.model_configurations = SqlPlatformModelConfigurationRepository(session)
        self.tenant_model_access = SqlTenantModelAccessRepository(session)
        self.users = SqlUserRepository(session)
        self.identities = SqlAuthIdentityRepository(session)
        self.credentials = SqlCredentialRepository(session)
        self.sessions = SqlSessionRepository(session)
        self.refresh_tokens = SqlRefreshTokenRepository(session)
        self.tenants = SqlTenantRepository(session)
        self.tenant_memberships = SqlTenantMembershipRepository(session)
        self.tenant_roles = SqlTenantRoleRepository(session)
        self.tenant_membership_roles = SqlTenantMembershipRoleRepository(session)
        self.role_hierarchy = SqlRoleHierarchyRepository(session)
        self.authorization_overrides = SqlAuthorizationOverrideRepository(session)
        self.impersonation_sessions = SqlImpersonationSessionRepository(session)
        self.audit = SqlAuditWriter(session)
        self.security_events = SqlSecurityEventWriter(session)

        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        assert self.session is not None
        try:
            if exc_type is None:
                await self.session.commit()
            else:
                await self.session.rollback()
        finally:
            await self.session.close()
