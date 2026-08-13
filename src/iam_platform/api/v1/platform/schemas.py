from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class CreateTenantRequest(BaseModel):
    slug: str
    display_name: str
    owner_user_id: str


class CreateTenantResponse(BaseModel):
    tenant_id: str


class TenantResponse(BaseModel):
    id: str
    slug: str
    display_name: str
    status: str
    owner_user_id: str
    created_at: str
    suspended_at: str | None
    suspended_reason: str | None


class SuspendTenantRequest(BaseModel):
    reason: str


class RenameTenantRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=200)


class GrantPlatformRoleRequest(BaseModel):
    target_user_id: str
    role_code: str


class RevokePlatformRoleRequest(BaseModel):
    target_user_id: str
    role_code: str


class EffectivePermissionsResponse(BaseModel):
    permissions: list[str]


class PlatformRoleResponse(BaseModel):
    id: str
    code: str
    name: str
    description: str | None
    is_system: bool
    rank: int


class PlatformPermissionResponse(BaseModel):
    code: str
    resource: str
    action: str
    description: str | None
    risk_level: str
    is_system: bool


class RolePermissionsResponse(BaseModel):
    """Role code -> the permission codes that role grants."""

    by_role_code: dict[str, list[str]]


class UserSummaryResponse(BaseModel):
    id: str
    email: str
    status: str
    email_verified: bool
    created_at: str
    last_login_at: str | None


class UserPageResponse(BaseModel):
    users: list[UserSummaryResponse]
    total: int
    limit: int
    offset: int


class UserMembershipResponse(BaseModel):
    membership_id: str
    tenant_id: str
    tenant_slug: str
    tenant_display_name: str
    status: str
    is_default: bool
    job_title: str | None


class UserDetailResponse(BaseModel):
    user: UserSummaryResponse
    platform_roles: list[str]
    platform_permissions: list[str]
    memberships: list[UserMembershipResponse]


class SetUserStatusRequest(BaseModel):
    reason: str | None = None


class CreatePlatformRoleRequest(BaseModel):
    code: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9_]+$")
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    rank: int = Field(ge=0)
    permission_codes: list[str] = []


class CreatePlatformRoleResponse(BaseModel):
    role_id: str


class CreateUserRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class CreateUserResponse(BaseModel):
    user_id: str
    email: str


class UpdateUserRequest(BaseModel):
    email: EmailStr


class CreateModelConfigurationRequest(BaseModel):
    """A model the platform offers to tenants.

    No `tenant_id` field: a configuration created here is platform-owned, and
    availability is a separate grant. Letting the request name an owner would
    reintroduce exactly the coupling entitlements exist to remove.
    """

    model_name: str = Field(min_length=1, max_length=200)
    parameters: dict[str, Any] = Field(default_factory=dict)
    token_budget_per_month: int | None = Field(default=None, ge=0)
    provider_credential_id: UUID | None = None


class UpdateModelConfigurationRequest(BaseModel):
    model_name: str = Field(min_length=1, max_length=200)
    parameters: dict[str, Any] = Field(default_factory=dict)
    token_budget_per_month: int | None = Field(default=None, ge=0)
    provider_credential_id: UUID | None = None


class CreateModelConfigurationResponse(BaseModel):
    id: UUID


class GrantModelConfigurationRequest(BaseModel):
    tenant_id: UUID


class TenantTokenUsageResponse(BaseModel):
    """What one tenant has spent against one configuration this month.

    Per *tenant*, not a total across them, because that is the number actually
    enforced -- a combined figure would read like the thing being checked while
    being a different quantity, which is the worst kind of dashboard.
    """

    tenant_id: UUID
    #: `None` means the counter could not be read, which is deliberately not
    #: rendered as 0: "unknown" and "nothing spent" are a whole budget apart.
    tokens_used_this_month: int | None


class PlatformModelConfigurationResponse(BaseModel):
    id: UUID
    model_name: str
    parameters: dict[str, Any]
    token_budget_per_month: int | None
    provider_credential_id: UUID | None
    #: Current-month spend per granted tenant, so a budget can be seen working
    #: rather than merely being set.
    tenant_usage: list[TenantTokenUsageResponse] = []
    #: True for rows created before entitlements existed, which belong to one
    #: tenant. Surfaced so an operator can tell them apart rather than
    #: wondering why a configuration they did not create is in the list.
    owning_tenant_id: UUID | None
    archived_at: datetime | None
    #: Tenants currently allowed to use this configuration.
    tenant_ids: list[UUID]
    created_at: datetime


class PlatformModelConfigurationListResponse(BaseModel):
    model_configurations: list[PlatformModelConfigurationResponse]
