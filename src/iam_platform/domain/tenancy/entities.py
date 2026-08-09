"""Tenancy domain entities -- see docs/13-schema-tenant-management.md.

`tenant_domains`, `tenant_settings`, `tenant_subscriptions`, and
`tenant_usage_limits` are intentionally not modeled yet -- see the Phase 6
scope note (CLAUDE.md): subdomain-based tenant resolution and plan/billing
semantics are deferred, and this module only needs enough to prove the
authorization model against a real tenant + membership.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from iam_platform.domain.shared.entity import Entity
from iam_platform.domain.shared.exceptions import InvalidStateTransitionError
from iam_platform.domain.shared.value_objects import Email


class TenantStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DEACTIVATED = "deactivated"


@dataclass(kw_only=True)
class Tenant(Entity):
    slug: str
    display_name: str
    status: TenantStatus = TenantStatus.PENDING
    owner_user_id: UUID
    region: str | None = None
    created_at: datetime
    updated_at: datetime
    suspended_at: datetime | None = None
    suspended_reason: str | None = None
    deleted_at: datetime | None = None

    @property
    def is_active(self) -> bool:
        return self.status == TenantStatus.ACTIVE and self.deleted_at is None

    def activate(self, *, now: datetime) -> None:
        if self.status not in (TenantStatus.PENDING, TenantStatus.SUSPENDED):
            raise InvalidStateTransitionError(f"cannot activate a {self.status} tenant")
        self.status = TenantStatus.ACTIVE
        self.suspended_at = None
        self.suspended_reason = None
        self.updated_at = now

    def suspend(self, *, reason: str, now: datetime) -> None:
        if self.status == TenantStatus.DEACTIVATED:
            raise InvalidStateTransitionError("cannot suspend a deactivated tenant")
        self.status = TenantStatus.SUSPENDED
        self.suspended_at = now
        self.suspended_reason = reason
        self.updated_at = now

    def rename(self, *, display_name: str, now: datetime) -> None:
        """Changes the human-readable name only.

        `slug` is deliberately not editable here: it's the tenant's identifier
        in URLs and (per docs/07) a candidate input to tenant resolution, so
        changing it after other systems may have recorded it is a much bigger
        operation than a rename -- effectively a re-provisioning, not an edit.
        """
        self.display_name = display_name
        self.updated_at = now


class MembershipStatus(StrEnum):
    INVITED = "invited"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    REVOKED = "revoked"


@dataclass(kw_only=True)
class TenantMembership(Entity):
    tenant_id: UUID
    user_id: UUID
    status: MembershipStatus = MembershipStatus.INVITED
    is_default: bool = False
    department_id: UUID | None = None
    team_id: UUID | None = None
    job_title: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    invited_by_user_id: UUID | None = None
    invited_at: datetime | None = None
    joined_at: datetime | None = None
    last_activity_at: datetime | None = None
    suspended_at: datetime | None = None
    suspended_reason: str | None = None
    revoked_at: datetime | None = None
    revoked_reason: str | None = None
    created_at: datetime
    updated_at: datetime

    @property
    def is_active(self) -> bool:
        return self.status == MembershipStatus.ACTIVE

    def accept_invitation(self, *, now: datetime) -> None:
        if self.status != MembershipStatus.INVITED:
            raise InvalidStateTransitionError(f"cannot accept from status {self.status}")
        self.status = MembershipStatus.ACTIVE
        self.joined_at = now
        self.updated_at = now

    def suspend(self, *, reason: str, now: datetime) -> None:
        if self.status != MembershipStatus.ACTIVE:
            raise InvalidStateTransitionError(f"cannot suspend from status {self.status}")
        self.status = MembershipStatus.SUSPENDED
        self.suspended_at = now
        self.suspended_reason = reason
        self.updated_at = now

    def reactivate(self, *, now: datetime) -> None:
        if self.status != MembershipStatus.SUSPENDED:
            raise InvalidStateTransitionError(f"cannot reactivate from status {self.status}")
        self.status = MembershipStatus.ACTIVE
        self.suspended_at = None
        self.suspended_reason = None
        self.updated_at = now

    def revoke(self, *, reason: str, now: datetime) -> None:
        if self.status == MembershipStatus.REVOKED:
            raise InvalidStateTransitionError("membership already revoked")
        self.status = MembershipStatus.REVOKED
        self.revoked_at = now
        self.revoked_reason = reason
        self.updated_at = now

    def restore(self, *, now: datetime) -> None:
        """Reverses a revocation, putting the person back in the tenant.

        `tenant_memberships` carries a unique `(tenant_id, user_id)` constraint,
        so once revoked there is no way to re-invite the same person -- a
        second membership row for the same pair can never be inserted. Without
        this transition, "I revoked the wrong person" was permanently
        unrecoverable through any API; the only way out was a manual database
        edit. Restoring re-activates the *same* row rather than creating a new
        one, which is what the unique constraint requires anyway.
        """
        if self.status != MembershipStatus.REVOKED:
            raise InvalidStateTransitionError(f"cannot restore from status {self.status}")
        self.status = MembershipStatus.ACTIVE
        self.revoked_at = None
        self.revoked_reason = None
        self.joined_at = now
        self.updated_at = now

    def record_activity(self, *, now: datetime) -> None:
        self.last_activity_at = now


class InvitationStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REVOKED = "revoked"
    EXPIRED = "expired"


@dataclass(kw_only=True)
class TenantInvitation(Entity):
    tenant_id: UUID
    email: Email
    invited_by_user_id: UUID
    role_ids: list[UUID]
    status: InvitationStatus = InvitationStatus.PENDING
    token_hash: str
    department_id: UUID | None = None
    team_id: UUID | None = None
    expires_at: datetime
    accepted_at: datetime | None = None
    accepted_by_user_id: UUID | None = None
    created_at: datetime

    def is_valid(self, *, now: datetime) -> bool:
        return self.status == InvitationStatus.PENDING and now < self.expires_at

    def accept(self, *, by_user_id: UUID, now: datetime) -> None:
        if not self.is_valid(now=now):
            raise InvalidStateTransitionError("invitation is not pending/valid")
        self.status = InvitationStatus.ACCEPTED
        self.accepted_at = now
        self.accepted_by_user_id = by_user_id

    def revoke(self) -> None:
        if self.status != InvitationStatus.PENDING:
            raise InvalidStateTransitionError(f"cannot revoke from status {self.status}")
        self.status = InvitationStatus.REVOKED
