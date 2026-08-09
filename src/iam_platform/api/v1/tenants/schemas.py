from __future__ import annotations

from pydantic import BaseModel, EmailStr


class TenantMembershipResponse(BaseModel):
    membership_id: str
    tenant_id: str
    status: str
    is_default: bool


class TenantMemberResponse(BaseModel):
    """A roster row -- deliberately carries `user_id`, not an email; see
    `application/tenancy/list_tenant_members.py` for why."""

    membership_id: str
    user_id: str
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
