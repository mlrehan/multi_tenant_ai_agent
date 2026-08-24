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


class TenantEntitlementsRequest(BaseModel):
    """A tenant's plan, as a platform operator sets it.

    `None` on any limit means **uncapped**, and is deliberately distinct from
    `0` ("none at all"). Both are accepted; the client sends `null` for the
    former, and the field is required rather than optional so an operator
    cannot half-fill the form and silently leave a limit at whatever it was.
    """

    max_knowledge_bases: int | None = Field(ge=0)
    max_chat_widgets: int | None = Field(ge=0)
    max_messages_per_day: int | None = Field(ge=0)
    max_tokens_per_month: int | None = Field(ge=0)
    allow_invite_members: bool
    allow_create_roles: bool


class TenantEntitlementsResponse(BaseModel):
    tenant_id: UUID
    max_knowledge_bases: int | None
    max_chat_widgets: int | None
    max_messages_per_day: int | None
    max_tokens_per_month: int | None
    allow_invite_members: bool
    allow_create_roles: bool
    updated_at: datetime


class TenantEntitlementsListResponse(BaseModel):
    entitlements: list[TenantEntitlementsResponse]


class ProviderCapabilityResponse(BaseModel):
    """What the console needs to disable the fields a provider cannot honour.

    `supported=False` entries are returned rather than hidden: an operator
    asking "why can't I pick Gemini?" deserves to see it listed as not yet
    implemented instead of wondering whether the page failed to load.
    """

    provider: str
    label: str
    supported: bool
    supports_embeddings: bool
    supports_embedding_dimensions: bool
    supports_reasoning_effort: bool
    supports_request_timeout: bool


class ProviderCapabilityListResponse(BaseModel):
    providers: list[ProviderCapabilityResponse]


# --- Platform overview dashboard ---------------------------------------------
#
# Every usage field below is `int | None`. `None` means the counter could not
# be read, which is deliberately distinct from `0` -- rendering an unreadable
# counter as zero would claim nothing has been spent, and the console is told
# to show `?` instead. The `running_low` / `remaining_*` flags are computed
# server-side so this screen and the tenant's own screen cannot disagree about
# what "running low" means.


class ProviderSpendResponse(BaseModel):
    provider: str
    model_count: int
    #: Sum of the per-tenant budgets for this provider's models. `None` when at
    #: least one configuration is unbudgeted, because a total that silently
    #: excludes the biggest spender is worse than no total.
    total_tokens: int | None
    used_tokens: int | None
    remaining_tokens: int | None
    running_low: bool
    has_unbudgeted: bool


class TenantModelSpendResponse(BaseModel):
    model_configuration_id: UUID
    model_name: str
    provider: str
    token_budget_per_month: int | None
    used_tokens: int | None


class TenantSpendResponse(BaseModel):
    tenant_id: UUID
    slug: str
    display_name: str
    max_tokens_per_month: int | None
    used_tokens: int | None
    remaining_tokens: int | None
    running_low: bool
    max_messages_per_day: int | None
    used_messages_today: int | None
    remaining_messages_today: int | None
    #: Per-model rows behind this tenant's total, for the drill-down modal.
    models: list[TenantModelSpendResponse] = []


class PlatformOverviewResponse(BaseModel):
    providers: list[ProviderSpendResponse]
    #: Tenants running low are returned first -- the ordering is the server's
    #: decision, so every client puts what needs attention at the top.
    tenants: list[TenantSpendResponse]
    tenants_running_low: int
    #: The threshold the flags above were computed with, so the console can
    #: explain *why* something is highlighted rather than restating a number
    #: that could drift from the server's.
    low_remaining_fraction: float
