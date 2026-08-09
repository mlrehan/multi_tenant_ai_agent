"""Self-service account reads and password change.

Everything here acts on the *caller's own* account, identified from the
access token's `sub` -- there is no target-user parameter to tamper with, so
these need no permission check beyond a valid session. That is deliberate:
the moment one of these grows an "on behalf of" argument it stops being
self-service and needs the platform-permission gate that
`application/platform_authz/manage_users.py` applies.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from iam_platform.application.identity.exceptions import (
    InvalidCredentialsError,
    UserNotFoundError,
    WeakPasswordError,
)
from iam_platform.application.identity.ports import IdentityUnitOfWork, PasswordHasher
from iam_platform.core.clock import Clock
from iam_platform.core.config import PasswordPolicySettings
from iam_platform.domain.identity.entities import IdentityKind
from iam_platform.domain.identity.policies import validate_password


@dataclass(frozen=True, slots=True)
class MfaMethodSummary:
    id: str
    type: str
    label: str | None
    is_primary: bool
    verified: bool
    created_at: datetime
    last_used_at: datetime | None


@dataclass(frozen=True, slots=True)
class LinkedProviderSummary:
    provider: str
    provider_email: str | None
    linked_at: datetime


@dataclass(frozen=True, slots=True)
class AccountProfile:
    user_id: str
    email: str
    status: str
    email_verified: bool
    created_at: datetime
    last_login_at: datetime | None
    has_password: bool
    mfa_methods: list[MfaMethodSummary]
    linked_providers: list[LinkedProviderSummary]


@dataclass(frozen=True, slots=True)
class GetMyAccountQuery:
    user_id: str


class GetMyAccount:
    """Assembles the identity summary the account screen needs.

    Note what is *not* here: no password hash, no TOTP secret, no
    `webauthn_public_key`. `MfaMethodSummary` has no field capable of
    carrying secret material, following the same shape-not-discipline rule
    as `ProviderCredentialSummary` in the AI-resources module -- a DTO that
    merely declines to populate a secret field is one careless edit away
    from leaking it.
    """

    def __init__(self, uow_factory: Callable[[], IdentityUnitOfWork]) -> None:
        self._uow_factory = uow_factory

    async def execute(self, query: GetMyAccountQuery) -> AccountProfile:
        user_id = UUID(query.user_id)

        async with self._uow_factory() as uow:
            user = await uow.users.get_by_id(user_id)
            if user is None:
                raise UserNotFoundError(query.user_id)

            methods = await uow.mfa_methods.list_by_user(user_id)
            identities = await uow.identities.list_by_user(user_id)

            has_password = any(
                i.kind == IdentityKind.PASSWORD and i.is_active for i in identities
            )

            linked: list[LinkedProviderSummary] = []
            for identity in identities:
                if identity.kind != IdentityKind.OAUTH or not identity.is_active:
                    continue
                account = await uow.oauth_accounts.get_by_identity_id(identity.id)
                if account is not None:
                    linked.append(
                        LinkedProviderSummary(
                            provider=account.provider,
                            provider_email=account.provider_email,
                            linked_at=account.linked_at,
                        )
                    )

            return AccountProfile(
                user_id=str(user.id),
                email=str(user.email),
                status=user.status.value,
                email_verified=user.email_verified_at is not None,
                created_at=user.created_at,
                last_login_at=user.last_login_at,
                has_password=has_password,
                mfa_methods=[
                    MfaMethodSummary(
                        id=str(m.id),
                        type=m.type.value,
                        label=m.label,
                        is_primary=m.is_primary,
                        verified=m.verified_at is not None,
                        created_at=m.created_at,
                        last_used_at=m.last_used_at,
                    )
                    for m in methods
                    if m.disabled_at is None
                ],
                linked_providers=linked,
            )


@dataclass(frozen=True, slots=True)
class ChangeMyPasswordCommand:
    user_id: str
    current_password: str
    new_password: str


class ChangeMyPassword:
    """Changes the caller's own password, proving possession of the old one.

    Unlike `ResetPassword` (which proves identity via an emailed token), this
    path is reachable with nothing but a live access token, so the current
    password is the only thing standing between a borrowed session and a
    permanent account takeover. It is verified before anything is written.
    """

    def __init__(
        self,
        uow_factory: Callable[[], IdentityUnitOfWork],
        password_hasher: PasswordHasher,
        password_policy: PasswordPolicySettings,
        clock: Clock,
    ) -> None:
        self._uow_factory = uow_factory
        self._hasher = password_hasher
        self._policy = password_policy
        self._clock = clock

    async def execute(self, command: ChangeMyPasswordCommand) -> None:
        violations = validate_password(
            command.new_password,
            min_length=self._policy.min_length,
            max_length=self._policy.max_length,
        )
        if violations:
            raise WeakPasswordError([v.message for v in violations])

        now = self._clock.now()
        user_id = UUID(command.user_id)

        # Set inside the transaction, acted on after it commits -- see the
        # `raise` note below.
        wrong_current_password = False

        async with self._uow_factory() as uow:
            user = await uow.users.get_by_id(user_id)
            if user is None:
                raise UserNotFoundError(command.user_id)

            identity = await uow.identities.get_by_user_and_kind(user_id, IdentityKind.PASSWORD)
            credential = (
                await uow.credentials.get_by_identity_id(identity.id)
                if identity is not None
                else None
            )
            # An OAuth-only account has no password to change. Reported as
            # bad credentials rather than a distinct error so the response
            # doesn't describe how the account authenticates. Nothing has been
            # written at this point, so raising here is safe.
            if identity is None or credential is None:
                raise InvalidCredentialsError

            if not self._hasher.verify(command.current_password, credential.password_hash):
                await uow.security_events.record(
                    user_id=user.id,
                    tenant_id=None,
                    event_type="identity.password_change_failed",
                    severity="warning",
                    details={"reason": "current_password_mismatch"},
                )
                # Deliberately NOT `raise` here. `__aexit__` rolls back on any
                # exception, so raising inside the block would discard the
                # security event just written -- exactly the trap documented in
                # docs/18-schema-rls-and-migrations.md ("A rollback pitfall
                # every Unit of Work implementation must avoid"). Exit the
                # block normally so the record commits, then raise.
                wrong_current_password = True
            else:
                credential.change_password(
                    new_hash=self._hasher.hash(command.new_password), now=now
                )
                # Same blast radius as a reset: every other session dies. The
                # caller's own client has to sign in again too, which is the
                # correct trade -- if the old password leaked, a still-live
                # session elsewhere is exactly what you're trying to kill.
                user.bump_security_stamp(new_stamp=uuid4(), now=now)

                await uow.credentials.save(credential)
                await uow.users.save(user)
                await uow.sessions.revoke_all_for_user(
                    user.id, reason="password_change", now=now
                )
                await uow.refresh_tokens.revoke_all_for_user(
                    user.id, reason="password_change", now=now
                )
                await uow.audit.record(
                    actor_user_id=user.id,
                    effective_user_id=user.id,
                    tenant_id=None,
                    action="identity.password_changed",
                    resource_type="user",
                    resource_id=user.id,
                    result="success",
                )

        if wrong_current_password:
            raise InvalidCredentialsError
