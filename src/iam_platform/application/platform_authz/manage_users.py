"""Platform-scope user directory and account lifecycle.

Distinct from `application/identity/account.py`, which is self-service: every
use case here acts on *another* user and is therefore permission-gated. Runs
on the BYPASSRLS platform connection because a user directory is inherently
cross-tenant.

Suspending an account is not the same as revoking a tenant membership
(`application/tenancy/manage_membership.py`): a suspension stops the person
signing in *anywhere*, while a membership revocation only removes them from
one tenant. The two are deliberately separate permissions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from iam_platform.application.identity.ports import PasswordHasher
from iam_platform.application.platform_authz.effective_permissions import (
    compute_effective_platform_state,
)
from iam_platform.application.platform_authz.exceptions import (
    DuplicateEmailError,
    UserManagementDeniedError,
    UserNotFoundError,
    WeakPasswordError,
)
from iam_platform.application.platform_authz.ports import PlatformUowFactory
from iam_platform.core.clock import Clock
from iam_platform.core.config import PasswordPolicySettings
from iam_platform.domain.identity.entities import (
    AuthIdentity,
    Credential,
    IdentityKind,
    User,
    UserStatus,
)
from iam_platform.domain.identity.policies import validate_password
from iam_platform.domain.shared.value_objects import Email

READ_PERMISSION = "platform.users.read"
MANAGE_PERMISSION = "platform.users.manage"

# Cap the page size regardless of what the client asks for -- an unbounded
# `limit` on a table sized for millions of rows is a denial-of-service vector
# that costs nothing to close here.
MAX_PAGE_SIZE = 100

__all__ = [
    "CreateUser",
    "CreateUserCommand",
    "CreatedUser",
    "DeleteUser",
    "DeleteUserCommand",
    "GetUser",
    "GetUserQuery",
    "ListUsers",
    "ListUsersQuery",
    "SetUserStatus",
    "SetUserStatusCommand",
    "UpdateUser",
    "UpdateUserCommand",
    "UserDetail",
    "UserManagementDeniedError",
    "UserPage",
    "UserSummary",
]


@dataclass(frozen=True, slots=True)
class UserSummary:
    id: str
    email: str
    status: str
    email_verified: bool
    created_at: datetime
    last_login_at: datetime | None


@dataclass(frozen=True, slots=True)
class UserPage:
    users: list[UserSummary]
    total: int
    limit: int
    offset: int


@dataclass(frozen=True, slots=True)
class TenantMembershipSummary:
    membership_id: str
    tenant_id: str
    tenant_slug: str
    tenant_display_name: str
    status: str
    is_default: bool
    job_title: str | None


@dataclass(frozen=True, slots=True)
class UserDetail:
    user: UserSummary
    platform_roles: list[str]
    platform_permissions: list[str]
    memberships: list[TenantMembershipSummary]


@dataclass(frozen=True, slots=True)
class ListUsersQuery:
    actor_user_id: str
    search: str | None = None
    limit: int = 25
    offset: int = 0


class ListUsers:
    def __init__(self, uow_factory: PlatformUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, query: ListUsersQuery) -> UserPage:
        actor_id = UUID(query.actor_user_id)
        limit = max(1, min(query.limit, MAX_PAGE_SIZE))
        offset = max(0, query.offset)

        async with self._uow_factory(actor_id) as uow:
            state = await compute_effective_platform_state(uow, actor_id, now=self._clock.now())
            if READ_PERMISSION not in state.permissions:
                raise UserManagementDeniedError(READ_PERMISSION)

            users, total = await uow.users.search(
                query=query.search, limit=limit, offset=offset
            )
            return UserPage(
                users=[_summarize(u) for u in users],
                total=total,
                limit=limit,
                offset=offset,
            )


@dataclass(frozen=True, slots=True)
class GetUserQuery:
    actor_user_id: str
    target_user_id: str


class GetUser:
    """One user, with the platform roles and tenant memberships they hold.

    This is the screen an administrator opens when asked "what can this person
    actually do?", so it resolves effective platform permissions rather than
    only listing role codes -- a role name alone doesn't answer the question.
    """

    def __init__(self, uow_factory: PlatformUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, query: GetUserQuery) -> UserDetail:
        actor_id = UUID(query.actor_user_id)
        target_id = UUID(query.target_user_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id) as uow:
            state = await compute_effective_platform_state(uow, actor_id, now=now)
            if READ_PERMISSION not in state.permissions:
                raise UserManagementDeniedError(READ_PERMISSION)

            user = await uow.users.get_by_id(target_id)
            if user is None:
                raise UserNotFoundError(query.target_user_id)

            target_state = await compute_effective_platform_state(uow, target_id, now=now)
            assignments = await uow.platform_user_roles.list_active_by_user(target_id)
            role_codes: list[str] = []
            for assignment in assignments:
                role = await uow.platform_roles.get_by_id(assignment.role_id)
                if role is not None:
                    role_codes.append(role.code)

            memberships: list[TenantMembershipSummary] = []
            for membership in await uow.tenant_memberships.list_by_user(target_id):
                tenant = await uow.tenants.get_by_id(membership.tenant_id)
                memberships.append(
                    TenantMembershipSummary(
                        membership_id=str(membership.id),
                        tenant_id=str(membership.tenant_id),
                        tenant_slug=tenant.slug if tenant else "(unknown)",
                        tenant_display_name=tenant.display_name if tenant else "(unknown)",
                        status=membership.status.value,
                        is_default=membership.is_default,
                        job_title=membership.job_title,
                    )
                )

            return UserDetail(
                user=_summarize(user),
                platform_roles=sorted(role_codes),
                platform_permissions=sorted(target_state.permissions),
                memberships=memberships,
            )


@dataclass(frozen=True, slots=True)
class SetUserStatusCommand:
    actor_user_id: str
    target_user_id: str
    suspend: bool
    reason: str | None = None


class SetUserStatus:
    """Suspends or reactivates a platform account.

    Suspension revokes every session and refresh token immediately and bumps
    the security stamp -- without that, a suspended user keeps working until
    their current access token expires, which is exactly the window that
    matters when you are suspending someone for cause.
    """

    def __init__(self, uow_factory: PlatformUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: SetUserStatusCommand) -> None:
        actor_id = UUID(command.actor_user_id)
        target_id = UUID(command.target_user_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id) as uow:
            state = await compute_effective_platform_state(uow, actor_id, now=now)
            if MANAGE_PERMISSION not in state.permissions:
                raise UserManagementDeniedError(MANAGE_PERMISSION)

            # Locking yourself out of the platform is never the intent, and
            # recovering from it needs database access (see
            # scripts/bootstrap_platform_admin.py). Refuse it outright.
            if actor_id == target_id and command.suspend:
                raise UserManagementDeniedError("cannot suspend your own account")

            user = await uow.users.get_by_id(target_id)
            if user is None:
                raise UserNotFoundError(command.target_user_id)

            # Through the entity's own transitions, not by assigning `status`
            # directly: `suspend()` refuses a deactivated (soft-deleted)
            # account and `reactivate()` refuses to resurrect one, which is
            # exactly the guard a raw field assignment skips.
            if command.suspend:
                user.suspend(now=now)
            else:
                user.reactivate(now=now)
            user.bump_security_stamp(new_stamp=uuid4(), now=now)
            await uow.users.save(user)

            if command.suspend:
                # Revoking here is what makes suspension immediate. The stamp
                # bump alone kills access tokens at their next freshness check,
                # but a live refresh token would still mint new ones.
                await uow.sessions.revoke_all_for_user(
                    target_id, reason="account_suspended", now=now
                )
                await uow.refresh_tokens.revoke_all_for_user(
                    target_id, reason="account_suspended", now=now
                )
                await uow.security_events.record(
                    user_id=target_id,
                    tenant_id=None,
                    event_type="platform.user_suspended",
                    severity="warning",
                    details={"actor": str(actor_id), "reason": command.reason},
                )

            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=target_id,
                tenant_id=None,
                action=(
                    "platform.user_suspended" if command.suspend else "platform.user_reactivated"
                ),
                resource_type="user",
                resource_id=target_id,
                result="success",
                metadata={"reason": command.reason} if command.reason else {},
            )


@dataclass(frozen=True, slots=True)
class CreateUserCommand:
    actor_user_id: str
    email: str
    password: str


@dataclass(frozen=True, slots=True)
class CreatedUser:
    user_id: str
    email: str


class CreateUser:
    """Provisions an account on someone else's behalf.

    Deliberately different from self-service `RegisterUser` in two ways:

    1. **It reports a duplicate email as a conflict.** `RegisterUser` returns
       success either way, because a public registration endpoint that says
       "already taken" is an account-enumeration oracle. That reasoning does
       not apply here: the caller already holds `platform.users.read` and can
       simply search the directory, so hiding it would only make the admin UI
       lie about whether the create worked.
    2. **The account starts active and email-unverified.** An administrator
       creating an account is the vouching step that email verification would
       otherwise provide, and this deployment cannot deliver mail at all (see
       `ConsoleEmailSender`). `email_verified_at` stays null so the record is
       honest about what was actually proven.

    The initial password is chosen by the caller and communicated out of band;
    it is never stored or echoed back, only hashed.
    """

    def __init__(
        self,
        uow_factory: PlatformUowFactory,
        password_hasher: PasswordHasher,
        password_policy: PasswordPolicySettings,
        clock: Clock,
    ) -> None:
        self._uow_factory = uow_factory
        self._hasher = password_hasher
        self._policy = password_policy
        self._clock = clock

    async def execute(self, command: CreateUserCommand) -> CreatedUser:
        violations = validate_password(
            command.password,
            min_length=self._policy.min_length,
            max_length=self._policy.max_length,
        )
        if violations:
            raise WeakPasswordError([v.message for v in violations])

        actor_id = UUID(command.actor_user_id)
        email = Email(command.email)
        now = self._clock.now()

        async with self._uow_factory(actor_id) as uow:
            state = await compute_effective_platform_state(uow, actor_id, now=now)
            if MANAGE_PERMISSION not in state.permissions:
                raise UserManagementDeniedError(MANAGE_PERMISSION)

            if await uow.users.get_by_email(email) is not None:
                raise DuplicateEmailError(command.email)

            user = User(
                id=uuid4(),
                email=email,
                status=UserStatus.ACTIVE,
                security_stamp=uuid4(),
                created_at=now,
                updated_at=now,
            )
            identity = AuthIdentity(
                id=uuid4(), user_id=user.id, kind=IdentityKind.PASSWORD, created_at=now
            )
            credential = Credential(
                id=uuid4(),
                identity_id=identity.id,
                password_hash=self._hasher.hash(command.password),
                password_updated_at=now,
            )

            await uow.users.add(user)
            await uow.identities.add(identity)
            await uow.credentials.add(credential)
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=user.id,
                tenant_id=None,
                action="platform.user_created",
                resource_type="user",
                resource_id=user.id,
                result="success",
                metadata={"email": str(email)},
            )

            return CreatedUser(user_id=str(user.id), email=str(email))


@dataclass(frozen=True, slots=True)
class UpdateUserCommand:
    actor_user_id: str
    target_user_id: str
    email: str


class UpdateUser:
    """Changes a user's login identifier.

    Resets email verification (via `User.change_email`) and bumps the security
    stamp: the address someone signs in with just changed, so every existing
    session should be re-established rather than silently carried over to a new
    identity.
    """

    def __init__(self, uow_factory: PlatformUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: UpdateUserCommand) -> None:
        actor_id = UUID(command.actor_user_id)
        target_id = UUID(command.target_user_id)
        email = Email(command.email)
        now = self._clock.now()

        async with self._uow_factory(actor_id) as uow:
            state = await compute_effective_platform_state(uow, actor_id, now=now)
            if MANAGE_PERMISSION not in state.permissions:
                raise UserManagementDeniedError(MANAGE_PERMISSION)

            user = await uow.users.get_by_id(target_id)
            if user is None:
                raise UserNotFoundError(command.target_user_id)

            if str(user.email) != str(email):
                clash = await uow.users.get_by_email(email)
                if clash is not None:
                    raise DuplicateEmailError(command.email)

                previous = str(user.email)
                user.change_email(new_email=email, now=now)
                user.bump_security_stamp(new_stamp=uuid4(), now=now)
                await uow.users.save(user)
                await uow.sessions.revoke_all_for_user(
                    target_id, reason="email_changed", now=now
                )
                await uow.refresh_tokens.revoke_all_for_user(
                    target_id, reason="email_changed", now=now
                )
                await uow.audit.record(
                    actor_user_id=actor_id,
                    effective_user_id=target_id,
                    tenant_id=None,
                    action="platform.user_email_changed",
                    resource_type="user",
                    resource_id=target_id,
                    result="success",
                    metadata={"from": previous, "to": str(email)},
                )


@dataclass(frozen=True, slots=True)
class DeleteUserCommand:
    actor_user_id: str
    target_user_id: str


class DeleteUser:
    """Soft-deletes an account.

    Never a hard delete: `audit_logs` and `security_events` reference the actor,
    and an IAM platform that can erase who did what has defeated its own
    repudiation defenses (docs/03-threat-model.md). The row stays, flagged
    `deleted_at`, and drops out of the directory (`UserRepository.search`
    filters on it).
    """

    def __init__(self, uow_factory: PlatformUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: DeleteUserCommand) -> None:
        actor_id = UUID(command.actor_user_id)
        target_id = UUID(command.target_user_id)
        now = self._clock.now()

        async with self._uow_factory(actor_id) as uow:
            state = await compute_effective_platform_state(uow, actor_id, now=now)
            if MANAGE_PERMISSION not in state.permissions:
                raise UserManagementDeniedError(MANAGE_PERMISSION)

            # Same reasoning as self-suspension: unrecoverable without direct
            # database access.
            if actor_id == target_id:
                raise UserManagementDeniedError("cannot delete your own account")

            user = await uow.users.get_by_id(target_id)
            if user is None:
                raise UserNotFoundError(command.target_user_id)

            user.soft_delete(now=now)
            user.bump_security_stamp(new_stamp=uuid4(), now=now)
            await uow.users.save(user)
            await uow.sessions.revoke_all_for_user(target_id, reason="account_deleted", now=now)
            await uow.refresh_tokens.revoke_all_for_user(
                target_id, reason="account_deleted", now=now
            )
            await uow.security_events.record(
                user_id=target_id,
                tenant_id=None,
                event_type="platform.user_deleted",
                severity="warning",
                details={"actor": str(actor_id)},
            )
            await uow.audit.record(
                actor_user_id=actor_id,
                effective_user_id=target_id,
                tenant_id=None,
                action="platform.user_deleted",
                resource_type="user",
                resource_id=target_id,
                result="success",
                metadata={},
            )


def _summarize(user: User) -> UserSummary:
    return UserSummary(
        id=str(user.id),
        email=str(user.email),
        status=user.status.value,
        email_verified=user.email_verified_at is not None,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
    )
