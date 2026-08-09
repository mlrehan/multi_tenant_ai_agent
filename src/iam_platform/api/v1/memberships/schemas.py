from __future__ import annotations

from pydantic import BaseModel


class MembershipActionRequest(BaseModel):
    reason: str = ""


class MembershipRoleResponse(BaseModel):
    """`role_id` only -- cross-reference against `GET /v1/tenants/{tenant_id}/roles`
    for the human-readable code/name/rank."""

    role_id: str
    granted_at: str


class AddMemberRequest(BaseModel):
    user_id: str
    role_codes: list[str] = []
    job_title: str | None = None


class AddMemberResponse(BaseModel):
    membership_id: str


class UpdateMembershipRequest(BaseModel):
    job_title: str | None = None
