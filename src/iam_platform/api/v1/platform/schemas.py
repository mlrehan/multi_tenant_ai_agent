from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
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
    #: The limit actually enforced today (ceiling lowered by the tenant's
    #: own preference). Show this, not the ceiling, as the allowance.
    effective_messages_per_day: int | None = None
    remaining_messages_today: int | None
    #: 80 / 90 / 95 / 100 once usage reaches that percentage, else null --
    #: the same rule the tenant's own plan screen applies.
    token_alert_level: int | None = None
    message_alert_level: int | None = None
    #: This month's tokens by direction (input includes the embedding). May
    #: sum to less than `used_tokens` for answers recorded before the split
    #: existed; null when the counter could not be read.
    input_tokens: int | None = None
    output_tokens: int | None = None
    #: Embedding tokens spent indexing documents this UTC month; not in
    #: `used_tokens`, which is the chat allowance.
    ingestion_tokens: int = 0
    #: Estimated cost this month at the platform's entered prices (chat and
    #: embeddings, ingestion included), as a decimal string. Tokens with no
    #: price in force are in `unpriced_tokens`, never costed at zero.
    cost_usd: Decimal = Decimal(0)
    unpriced_tokens: int = 0
    #: Per-model rows behind this tenant's total, for the drill-down modal.
    models: list[TenantModelSpendResponse] = []


class PlatformOverviewResponse(BaseModel):
    providers: list[ProviderSpendResponse]
    #: Tenants running low are returned first -- the ordering is the server's
    #: decision, so every client puts what needs attention at the top.
    tenants: list[TenantSpendResponse]
    tenants_running_low: int
    #: Tokens spent on the platform default model, which belongs to no
    #: configuration and therefore to no provider row. Providers +
    #: unattributed = what tenants actually spent; without it the two panels
    #: on this page disagree with no explanation. `None` when a counter could
    #: not be read.
    unattributed_tokens: int | None = None
    #: All tenants' ingestion embeddings this month.
    ingestion_tokens: int = 0
    #: This month's estimated cost. Optional so an older client ignores it.
    costs: CostSummaryResponse | None = None
    #: The threshold the flags above were computed with, so the console can
    #: explain *why* something is highlighted rather than restating a number
    #: that could drift from the server's.
    low_remaining_fraction: float


class DailyActivityResponse(BaseModel):
    day: date
    conversations_started: int
    questions: int
    answers: int
    handoffs: int
    tokens: int


class PeriodComparisonResponse(BaseModel):
    current: int
    previous: int


class TenantAttentionResponse(BaseModel):
    tenant_id: UUID
    display_name: str
    slug: str
    waiting_handoffs: int
    oldest_waiting_at: datetime | None
    stuck_documents: int
    failed_documents: int


class DependencyHealthResponse(BaseModel):
    #: A stable identifier (`postgres_tenant`, `postgres_platform`, `redis`).
    name: str
    healthy: bool


class PlatformActivityResponse(BaseModel):
    generated_at: datetime
    #: Oldest first, UTC days, zero-filled -- a quiet day is 0, never absent.
    daily: list[DailyActivityResponse]
    trend_days: int
    questions: PeriodComparisonResponse
    conversations: PeriodComparisonResponse
    handoffs: PeriodComparisonResponse
    satisfaction_days: int
    helpful: PeriodComparisonResponse
    not_helpful: PeriodComparisonResponse
    active_tenants: int
    waiting_handoffs: int
    handled_handoffs: int
    oldest_waiting_at: datetime | None
    documents_processing: int
    documents_stuck: int
    documents_failed: int
    stuck_after_minutes: int
    attention: list[TenantAttentionResponse]
    usage_recorded_since: datetime | None
    #: This API process's view of its own dependencies -- the same probe as
    #: `/readyz`. Failure details are omitted: they can carry hostnames.
    health: list[DependencyHealthResponse]


class PlatformFeedbackItem(BaseModel):
    id: UUID
    tenant_id: UUID
    tenant_name: str | None
    rating: str
    question: str
    answer: str
    comment: str | None
    channel: str
    knowledge_base_name: str | None
    #: The member who rated it from the console; `None` for a website visitor.
    author_email: str | None
    created_at: datetime


class PlatformFeedbackTenantSummary(BaseModel):
    tenant_id: UUID
    tenant_name: str | None
    total: int
    helpful: int
    not_helpful: int


class PlatformFeedbackResponse(BaseModel):
    items: list[PlatformFeedbackItem]
    total: int
    by_tenant: list[PlatformFeedbackTenantSummary]


# --- estimated cost and the price list ---------------------------------------
#
# Money is a Decimal, serialised as a string: a price is per *million* tokens
# and one answer costs fractions of a cent, so a float would round visibly.


class ModelCostResponse(BaseModel):
    model: str | None
    #: "chat" or "embedding".
    kind: str
    tokens: int
    unpriced_tokens: int
    cost_usd: Decimal


class CostSummaryResponse(BaseModel):
    """An estimate: tokens times the prices entered here, not an invoice."""

    currency: str = "USD"
    total_usd: Decimal
    priced_tokens: int
    #: Tokens with no price in force, or no recorded model. Counted, never
    #: costed at zero -- a total that silently dropped them would understate.
    unpriced_tokens: int
    by_model: list[ModelCostResponse]


class ModelPriceResponse(BaseModel):
    id: UUID
    model_name: str
    input_usd_per_million: Decimal
    output_usd_per_million: Decimal
    effective_from: datetime
    created_at: datetime


class ModelInUseResponse(BaseModel):
    model: str
    kind: str
    tokens_this_month: int
    current: ModelPriceResponse | None


class ModelPriceCatalogueResponse(BaseModel):
    prices: list[ModelPriceResponse]
    models_in_use: list[ModelInUseResponse]


class SetModelPriceRequest(BaseModel):
    model_name: str = Field(min_length=1, max_length=200)
    input_usd_per_million: Decimal = Field(ge=0)
    #: Embedding models have no output; 0 is the honest value for them.
    output_usd_per_million: Decimal = Field(default=Decimal(0), ge=0)
    #: Omitted = from now. Earlier = prices usage already recorded.
    effective_from: datetime | None = None


class CreateModelPriceResponse(BaseModel):
    id: UUID



# Declared above the cost models it references.
PlatformOverviewResponse.model_rebuild()
