"""OAuth login, JIT registration, linking and unlinking -- docs/05-authentication-flows.md.

The state/nonce/PKCE dance and id_token/signature verification happen in the
``infrastructure.oauth`` adapter (invoked from the ``api`` layer) before this
module is ever reached -- everything here operates on an already-verified
``OAuthProfile``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

from iam_platform.application.identity.dto import LoginResult, LoginStatus
from iam_platform.application.identity.exceptions import (
    CannotUnlinkLastAuthMethodError,
    InvalidCredentialsError,
    OAuthEmailConflictError,
)
from iam_platform.application.identity.ports import IdentityUnitOfWork, JwtIssuer, OAuthProfile
from iam_platform.application.identity.session_issuance import create_session_and_tokens
from iam_platform.core.clock import Clock
from iam_platform.domain.identity.entities import AuthIdentity, IdentityKind, OAuthAccount, User
from iam_platform.domain.shared.value_objects import Email


@dataclass(frozen=True, slots=True)
class CompleteOAuthLoginCommand:
    profile: OAuthProfile
    linking_user_id: str | None = None
    ip: str | None = None
    user_agent: str | None = None


class CompleteOAuthLogin:
    def __init__(
        self,
        uow_factory: Callable[[], IdentityUnitOfWork],
        jwt_issuer: JwtIssuer,
        clock: Clock,
        access_token_ttl_seconds: int,
    ) -> None:
        self._uow_factory = uow_factory
        self._jwt_issuer = jwt_issuer
        self._clock = clock
        self._access_token_ttl_seconds = access_token_ttl_seconds

    async def execute(self, command: CompleteOAuthLoginCommand) -> LoginResult | None:
        profile = command.profile

        async with self._uow_factory() as uow:
            existing_account = await uow.oauth_accounts.get_by_provider_subject(
                provider=profile.provider, subject=profile.subject
            )

            if existing_account is not None:
                return await self._login_existing(uow, existing_account, command)

            if command.linking_user_id is not None:
                await self._link_to_current_user(uow, command)
                return None

            return await self._register_via_oauth(uow, command)

    async def _login_existing(
        self,
        uow: IdentityUnitOfWork,
        account: OAuthAccount,
        command: CompleteOAuthLoginCommand,
    ) -> LoginResult:
        now = self._clock.now()
        identity = await uow.identities.get_by_id(account.identity_id)
        if identity is None or not identity.is_active:
            raise InvalidCredentialsError
        user = await uow.users.get_by_id(identity.user_id)
        if user is None or not user.is_active:
            raise InvalidCredentialsError

        identity.record_use(now=now)
        await uow.identities.save(identity)
        user.record_login(now=now)
        await uow.users.save(user)

        tokens = await create_session_and_tokens(
            uow,
            self._jwt_issuer,
            user=user,
            amr=["oauth"],
            now=now,
            ip=command.ip,
            user_agent=command.user_agent,
            access_token_ttl_seconds=self._access_token_ttl_seconds,
        )
        await uow.audit.record(
            actor_user_id=user.id,
            effective_user_id=user.id,
            tenant_id=None,
            action="identity.oauth_login",
            resource_type="user",
            resource_id=user.id,
            result="success",
            ip=command.ip,
            user_agent=command.user_agent,
            metadata={"provider": command.profile.provider},
        )
        return LoginResult(status=LoginStatus.SUCCESS, tokens=tokens)

    async def _link_to_current_user(
        self, uow: IdentityUnitOfWork, command: CompleteOAuthLoginCommand
    ) -> None:
        now = self._clock.now()
        assert command.linking_user_id is not None
        user = await uow.users.get_by_id(UUID(command.linking_user_id))
        if user is None:
            raise InvalidCredentialsError

        identity = AuthIdentity(id=uuid4(), user_id=user.id, kind=IdentityKind.OAUTH, created_at=now)
        oauth_account = OAuthAccount(
            id=uuid4(),
            identity_id=identity.id,
            provider=command.profile.provider,
            provider_subject=command.profile.subject,
            provider_email=command.profile.email,
            raw_profile=command.profile.raw_profile,
            linked_at=now,
        )
        await uow.identities.add(identity)
        await uow.oauth_accounts.add(oauth_account)
        await uow.audit.record(
            actor_user_id=user.id,
            effective_user_id=user.id,
            tenant_id=None,
            action="identity.oauth_linked",
            resource_type="user",
            resource_id=user.id,
            result="success",
            ip=command.ip,
            user_agent=command.user_agent,
            metadata={"provider": command.profile.provider},
        )

    async def _register_via_oauth(
        self, uow: IdentityUnitOfWork, command: CompleteOAuthLoginCommand
    ) -> LoginResult:
        now = self._clock.now()
        profile = command.profile

        if profile.email is not None:
            conflict = await uow.users.get_by_email(Email(profile.email))
            if conflict is not None:
                # An account with this email already exists via a different auth
                # method -- never silently merge (docs/05-authentication-flows.md).
                raise OAuthEmailConflictError

        email_value = profile.email or f"{profile.provider}-{profile.subject}@oauth.invalid"
        user = User(
            id=uuid4(),
            email=Email(email_value),
            security_stamp=uuid4(),
            created_at=now,
            updated_at=now,
        )
        if profile.email is not None:
            user.mark_email_verified(now=now)  # the IdP already verified it

        identity = AuthIdentity(id=uuid4(), user_id=user.id, kind=IdentityKind.OAUTH, created_at=now)
        oauth_account = OAuthAccount(
            id=uuid4(),
            identity_id=identity.id,
            provider=profile.provider,
            provider_subject=profile.subject,
            provider_email=profile.email,
            raw_profile=profile.raw_profile,
            linked_at=now,
        )
        await uow.users.add(user)
        await uow.identities.add(identity)
        await uow.oauth_accounts.add(oauth_account)

        tokens = await create_session_and_tokens(
            uow,
            self._jwt_issuer,
            user=user,
            amr=["oauth"],
            now=now,
            ip=command.ip,
            user_agent=command.user_agent,
            access_token_ttl_seconds=self._access_token_ttl_seconds,
        )
        await uow.audit.record(
            actor_user_id=user.id,
            effective_user_id=user.id,
            tenant_id=None,
            action="identity.oauth_register",
            resource_type="user",
            resource_id=user.id,
            result="success",
            ip=command.ip,
            user_agent=command.user_agent,
            metadata={"provider": profile.provider},
        )
        return LoginResult(status=LoginStatus.SUCCESS, tokens=tokens)


@dataclass(frozen=True, slots=True)
class UnlinkOAuthAccountCommand:
    user_id: str
    identity_id: str


class UnlinkOAuthAccount:
    def __init__(self, uow_factory: Callable[[], IdentityUnitOfWork], clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: UnlinkOAuthAccountCommand) -> None:
        now = self._clock.now()
        user_id = UUID(command.user_id)
        identity_id = UUID(command.identity_id)

        async with self._uow_factory() as uow:
            identities = await uow.identities.list_by_user(user_id)
            target = next((i for i in identities if i.id == identity_id), None)
            if target is None:
                raise InvalidCredentialsError

            other_active = [i for i in identities if i.is_active and i.id != identity_id]
            if not other_active:
                raise CannotUnlinkLastAuthMethodError

            target.revoke(now=now)
            await uow.identities.save(target)
            await uow.audit.record(
                actor_user_id=user_id,
                effective_user_id=user_id,
                tenant_id=None,
                action="identity.oauth_unlinked",
                resource_type="user",
                resource_id=user_id,
                result="success",
            )
