from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class EffectivePermissionsResponse(BaseModel):
    permissions: list[str]


class TenantRoleResponse(BaseModel):
    id: str
    code: str
    name: str
    description: str | None
    is_system: bool
    rank: int


class TenantPermissionResponse(BaseModel):
    code: str
    resource: str
    action: str
    description: str | None
    risk_level: str
    is_system: bool
    tenant_customizable: bool
    required_feature: str | None


class CreateCustomRoleRequest(BaseModel):
    code: str
    name: str
    description: str | None = None
    rank: int
    permission_codes: list[str]


class CreateCustomRoleResponse(BaseModel):
    role_id: str


class CreateRoleHierarchyEdgeRequest(BaseModel):
    parent_role_code: str
    child_role_code: str


class AssignRoleRequest(BaseModel):
    role_code: str


class CreateOverrideRequest(BaseModel):
    target_membership_id: str
    permission_code: str
    effect: str
    reason: str
    expires_at: datetime | None = None


class CreateOverrideResponse(BaseModel):
    override_id: str


class RolePermissionsResponse(BaseModel):
    """What each role *definition* grants. Not the same as a member's
    effective permissions -- hierarchy inheritance and overrides apply on top
    (see `/me/effective-permissions`)."""

    by_role_code: dict[str, list[str]]
