from __future__ import annotations

from pydantic import BaseModel, EmailStr


class TenantMembershipResponse(BaseModel):
    membership_id: str
    tenant_id: str
    status: str
    is_default: bool
    #: For the tenant switcher. Null for a revoked membership, whose tenant's
    #: current name the former member is not told.
    tenant_slug: str | None = None
    tenant_display_name: str | None = None


class TenantMemberResponse(BaseModel):
    """A roster row. `email`/`display_name` come from a two-field projection
    scoped to this tenant's own members -- see `TenantMemberDirectory`."""

    membership_id: str
    user_id: str
    #: None for a deleted account (its row is kept for the audit trail).
    email: str | None = None
    display_name: str | None = None
    status: str
    is_default: bool
    department_id: str | None
    team_id: str | None
    job_title: str | None
    created_at: str


class InviteMemberRequest(BaseModel):
    email: EmailStr
    role_codes: list[str]


class AcceptInvitationRequest(BaseModel):
    token: str
