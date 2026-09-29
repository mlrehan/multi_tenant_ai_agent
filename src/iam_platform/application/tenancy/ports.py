"""Tenancy repository ports -- shared by ``platform_authz`` (tenant creation,
cross-tenant support lookups, BYPASSRLS) and ``tenant_authz`` (membership/
invitation management, RLS-subject) rather than duplicated between them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from iam_platform.domain.tenancy.entities import Tenant, TenantInvitation, TenantMembership


class TenantRepository(Protocol):
    async def get_by_id(self, tenant_id: UUID) -> Tenant | None: ...
    async def get_by_slug(self, slug: str) -> Tenant | None: ...
    async def list_all(self) -> list[Tenant]:
        """Every tenant, platform-wide. Only ever called from the BYPASSRLS
        platform connection (`PlatformUnitOfWork`) -- there is no tenant
        context to scope this by, by definition."""
        ...
    async def add(self, tenant: Tenant) -> None: ...
    async def save(self, tenant: Tenant) -> None: ...


@dataclass(frozen=True, slots=True)
class MemberContact:
    """How a tenant's roster names one of its own members.

    Two fields and nothing else -- no status, no login history, no other
    tenants. `email` is None for a deleted account: its row is kept for the
    audit trail, but the address no longer identifies anyone who can act.
    """

    email: str | None
    display_name: str | None


class TenantMemberDirectory(Protocol):
    """Names for a tenant's members, so an administrator sees people rather
    than UUIDs.

    **A projection, deliberately, not the identity repositories.** The tenant
    side of this codebase has never been handed `users`, and still is not:
    this returns two fields, for memberships *of the named tenant only*. That
    scoping is the whole point, and it cannot be left to RLS -- `users` has no
    RLS policy at all, and `tenant_memberships`' policy also admits the
    caller's own memberships in *other* tenants (the self-lookup exception).
    The implementation therefore filters by tenant explicitly.
    """

    async def contacts(self, tenant_id: UUID) -> dict[UUID, MemberContact]:
        """Keyed by membership id."""
        ...


class TenantMembershipRepository(Protocol):
    async def get_by_id(self, membership_id: UUID) -> TenantMembership | None: ...
    async def get_by_tenant_and_user(
        self, tenant_id: UUID, user_id: UUID
    ) -> TenantMembership | None: ...
    async def list_by_tenant(self, tenant_id: UUID) -> list[TenantMembership]: ...
    async def list_by_user(self, user_id: UUID) -> list[TenantMembership]:
        """Cross-tenant by design (a user may belong to several tenants) --
        only usable from a UnitOfWork whose RLS context includes the
        self-lookup exception (``user_id = current_setting('app.user_id')``)
        or one that bypasses RLS entirely. See
        docs/18-schema-rls-and-migrations.md (amended in Phase 6) for the
        policy that makes this safe under RLS without a platform bypass."""
        ...

    async def add(self, membership: TenantMembership) -> None: ...
    async def save(self, membership: TenantMembership) -> None: ...


class TenantInvitationRepository(Protocol):
    async def get_by_token_hash(self, token_hash: str) -> TenantInvitation | None: ...
    async def get_pending_by_tenant_and_email(
        self, tenant_id: UUID, email: str
    ) -> TenantInvitation | None: ...
    async def add(self, invitation: TenantInvitation) -> None: ...
    async def save(self, invitation: TenantInvitation) -> None: ...


class TenantFeatureRepository(Protocol):
    async def list_enabled_codes(self, tenant_id: UUID) -> set[str]: ...
    async def enable(self, tenant_id: UUID, feature_code: str, *, now: datetime) -> None: ...


class InvitationEmailSender(Protocol):
    async def send_invitation_email(self, *, to: str, token: str, tenant_name: str) -> None: ...
