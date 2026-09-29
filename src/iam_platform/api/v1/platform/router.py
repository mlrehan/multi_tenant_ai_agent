"""``/v1/platform/*`` -- platform-scope tenant lifecycle and role management.

No route-level permission dependency: each use case enforces its own
required permission internally (via ``compute_effective_platform_state``),
which keeps the use cases safe to call from anywhere (a worker, a script)
without depending on the API's dependency chain, and avoids computing
effective permissions twice per request now that there's no cache in front
of that computation (Phase 6 scope note, CLAUDE.md).
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status

from iam_platform.api.deps.authn import get_container, get_current_claims
from iam_platform.api.deps.container import AppContainer
from iam_platform.api.v1.platform import schemas
from iam_platform.application.ai_resources.manage_entitlements import (
    ListTenantEntitlements,
    ListTenantEntitlementsQuery,
    SetTenantEntitlements,
    SetTenantEntitlementsCommand,
)
from iam_platform.application.ai_resources.manage_model_configuration import (
    ArchiveModelConfiguration,
    CreateModelConfiguration,
    CreateModelConfigurationCommand,
    GrantModelConfigurationToTenant,
    ListModelConfigurationsForPlatform,
    ListModelConfigurationsForPlatformQuery,
    ModelConfigurationActionCommand,
    ModelConfigurationWithAccess,
    RestoreModelConfiguration,
    RevokeModelConfigurationFromTenant,
    TenantAccessCommand,
    UpdateModelConfiguration,
    UpdateModelConfigurationCommand,
)
from iam_platform.application.ai_resources.platform_activity import (
    SATISFACTION_DAYS,
    STUCK_AFTER,
    TREND_DAYS,
    GetPlatformActivity,
    PeriodComparison,
    PlatformActivityQuery,
)
from iam_platform.application.ai_resources.platform_overview import (
    LOW_REMAINING_FRACTION,
    GetPlatformOverview,
    PlatformOverviewQuery,
)
from iam_platform.application.ai_resources.usage_costs import (
    DeleteModelPrice,
    DeleteModelPriceCommand,
    ListModelPrices,
    ListModelPricesQuery,
    SetModelPrice,
    SetModelPriceCommand,
)
from iam_platform.application.identity.ports import AccessTokenClaims
from iam_platform.application.platform_authz.effective_permissions import (
    ResolvePlatformEffectivePermissions,
    ResolvePlatformEffectivePermissionsQuery,
)
from iam_platform.application.platform_authz.grant_platform_role import (
    GrantPlatformRole,
    GrantPlatformRoleCommand,
    RevokePlatformRole,
    RevokePlatformRoleCommand,
)
from iam_platform.application.platform_authz.list_catalog import (
    ListPlatformPermissions,
    ListPlatformRolePermissions,
    ListPlatformRoles,
    PlatformCatalogQuery,
)
from iam_platform.application.platform_authz.list_tenants import ListTenants, ListTenantsQuery
from iam_platform.application.platform_authz.manage_custom_role import (
    AddPermissionToPlatformRole,
    CreateCustomPlatformRole,
    CreateCustomPlatformRoleCommand,
    PlatformRolePermissionCommand,
    RemovePermissionFromPlatformRole,
)
from iam_platform.application.platform_authz.manage_tenants import (
    CreateTenant,
    CreateTenantCommand,
    ReactivateTenant,
    ReactivateTenantCommand,
    RenameTenant,
    RenameTenantCommand,
    SuspendTenant,
    SuspendTenantCommand,
)
from iam_platform.application.platform_authz.manage_users import (
    CreateUser,
    CreateUserCommand,
    DeleteUser,
    DeleteUserCommand,
    GetUser,
    GetUserQuery,
    ListUsers,
    ListUsersQuery,
    SetUserStatus,
    SetUserStatusCommand,
    UpdateUser,
    UpdateUserCommand,
    UserSummary,
)
from iam_platform.domain.ai_resources.pricing import ModelPrice
from iam_platform.domain.ai_resources.providers import all_capabilities
from iam_platform.domain.tenancy.entitlements import TenantEntitlements

logger = logging.getLogger("iam_platform.api.v1.platform")

router = APIRouter(prefix="/v1/platform", tags=["platform"])


def _user_summary(u: UserSummary) -> schemas.UserSummaryResponse:
    return schemas.UserSummaryResponse(
        id=u.id,
        email=u.email,
        status=u.status,
        email_verified=u.email_verified,
        created_at=u.created_at.isoformat(),
        last_login_at=u.last_login_at.isoformat() if u.last_login_at else None,
    )


@router.get("/tenants", response_model=list[schemas.TenantResponse])
async def list_tenants(
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> list[schemas.TenantResponse]:
    use_case = ListTenants(container.platform_uow_factory, container.clock)
    tenants = await use_case.execute(ListTenantsQuery(actor_user_id=str(claims.user_id)))
    return [
        schemas.TenantResponse(
            id=str(t.id),
            slug=t.slug,
            display_name=t.display_name,
            status=t.status.value,
            owner_user_id=str(t.owner_user_id),
            created_at=t.created_at.isoformat(),
            suspended_at=t.suspended_at.isoformat() if t.suspended_at else None,
            suspended_reason=t.suspended_reason,
        )
        for t in tenants
    ]


@router.post("/tenants", status_code=status.HTTP_201_CREATED, response_model=schemas.CreateTenantResponse)
async def create_tenant(
    body: schemas.CreateTenantRequest,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> schemas.CreateTenantResponse:
    use_case = CreateTenant(container.platform_uow_factory, container.clock)
    tenant_id = await use_case.execute(
        CreateTenantCommand(
            actor_user_id=str(claims.user_id),
            slug=body.slug,
            display_name=body.display_name,
            owner_user_id=body.owner_user_id,
        )
    )
    return schemas.CreateTenantResponse(tenant_id=str(tenant_id))


@router.post("/tenants/{tenant_id}/suspend", status_code=status.HTTP_204_NO_CONTENT)
async def suspend_tenant(
    tenant_id: str,
    body: schemas.SuspendTenantRequest,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> None:
    use_case = SuspendTenant(container.platform_uow_factory, container.clock)
    await use_case.execute(
        SuspendTenantCommand(actor_user_id=str(claims.user_id), tenant_id=tenant_id, reason=body.reason)
    )


@router.post("/tenants/{tenant_id}/reactivate", status_code=status.HTTP_204_NO_CONTENT)
async def reactivate_tenant(
    tenant_id: str,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> None:
    use_case = ReactivateTenant(container.platform_uow_factory, container.clock)
    await use_case.execute(
        ReactivateTenantCommand(actor_user_id=str(claims.user_id), tenant_id=tenant_id)
    )


@router.patch("/tenants/{tenant_id}", status_code=status.HTTP_204_NO_CONTENT)
async def rename_tenant(
    tenant_id: str,
    body: schemas.RenameTenantRequest,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> None:
    use_case = RenameTenant(container.platform_uow_factory, container.clock)
    await use_case.execute(
        RenameTenantCommand(
            actor_user_id=str(claims.user_id), tenant_id=tenant_id, display_name=body.display_name
        )
    )


@router.post("/roles/grant", status_code=status.HTTP_204_NO_CONTENT)
async def grant_platform_role(
    body: schemas.GrantPlatformRoleRequest,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> None:
    use_case = GrantPlatformRole(container.platform_uow_factory, container.clock)
    await use_case.execute(
        GrantPlatformRoleCommand(
            actor_user_id=str(claims.user_id),
            target_user_id=body.target_user_id,
            role_code=body.role_code,
        )
    )


@router.post("/roles/revoke", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_platform_role(
    body: schemas.RevokePlatformRoleRequest,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> None:
    use_case = RevokePlatformRole(container.platform_uow_factory, container.clock)
    await use_case.execute(
        RevokePlatformRoleCommand(
            actor_user_id=str(claims.user_id),
            target_user_id=body.target_user_id,
            role_code=body.role_code,
        )
    )


@router.post(
    "/roles", status_code=status.HTTP_201_CREATED, response_model=schemas.CreatePlatformRoleResponse
)
async def create_platform_role(
    body: schemas.CreatePlatformRoleRequest,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> schemas.CreatePlatformRoleResponse:
    use_case = CreateCustomPlatformRole(container.platform_uow_factory, container.clock)
    role_id = await use_case.execute(
        CreateCustomPlatformRoleCommand(
            actor_user_id=str(claims.user_id),
            code=body.code,
            name=body.name,
            description=body.description,
            rank=body.rank,
            permission_codes=body.permission_codes,
        )
    )
    return schemas.CreatePlatformRoleResponse(role_id=str(role_id))


@router.post("/roles/{role_code}/permissions/{permission_code}", status_code=status.HTTP_204_NO_CONTENT)
async def add_permission_to_platform_role(
    role_code: str,
    permission_code: str,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> None:
    use_case = AddPermissionToPlatformRole(container.platform_uow_factory, container.clock)
    await use_case.execute(
        PlatformRolePermissionCommand(
            actor_user_id=str(claims.user_id), role_code=role_code, permission_code=permission_code
        )
    )


@router.delete(
    "/roles/{role_code}/permissions/{permission_code}", status_code=status.HTTP_204_NO_CONTENT
)
async def remove_permission_from_platform_role(
    role_code: str,
    permission_code: str,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> None:
    use_case = RemovePermissionFromPlatformRole(container.platform_uow_factory, container.clock)
    await use_case.execute(
        PlatformRolePermissionCommand(
            actor_user_id=str(claims.user_id), role_code=role_code, permission_code=permission_code
        )
    )


@router.get("/roles", response_model=list[schemas.PlatformRoleResponse])
async def list_platform_roles(
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> list[schemas.PlatformRoleResponse]:
    use_case = ListPlatformRoles(container.platform_uow_factory)
    roles = await use_case.execute(PlatformCatalogQuery(actor_user_id=str(claims.user_id)))
    return [
        schemas.PlatformRoleResponse(
            id=str(r.id),
            code=r.code,
            name=r.name,
            description=r.description,
            is_system=r.is_system,
            rank=r.rank,
        )
        for r in roles
    ]


@router.get("/permissions", response_model=list[schemas.PlatformPermissionResponse])
async def list_platform_permissions(
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> list[schemas.PlatformPermissionResponse]:
    use_case = ListPlatformPermissions(container.platform_uow_factory)
    permissions = await use_case.execute(PlatformCatalogQuery(actor_user_id=str(claims.user_id)))
    return [
        schemas.PlatformPermissionResponse(
            code=p.code,
            resource=p.resource,
            action=p.action,
            description=p.description,
            risk_level=p.risk_level,
            is_system=p.is_system,
        )
        for p in permissions
    ]


@router.get("/roles/permissions", response_model=schemas.RolePermissionsResponse)
async def list_platform_role_permissions(
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> schemas.RolePermissionsResponse:
    use_case = ListPlatformRolePermissions(container.platform_uow_factory)
    mapping = await use_case.execute(PlatformCatalogQuery(actor_user_id=str(claims.user_id)))
    return schemas.RolePermissionsResponse(by_role_code=mapping.by_role_code)


@router.get("/users", response_model=schemas.UserPageResponse)
async def list_users(
    search: str | None = None,
    limit: int = 25,
    offset: int = 0,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> schemas.UserPageResponse:
    use_case = ListUsers(container.platform_uow_factory, container.clock)
    page = await use_case.execute(
        ListUsersQuery(
            actor_user_id=str(claims.user_id), search=search, limit=limit, offset=offset
        )
    )
    return schemas.UserPageResponse(
        users=[_user_summary(u) for u in page.users],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post(
    "/users", status_code=status.HTTP_201_CREATED, response_model=schemas.CreateUserResponse
)
async def create_user(
    body: schemas.CreateUserRequest,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> schemas.CreateUserResponse:
    use_case = CreateUser(
        container.platform_uow_factory,
        container.password_hasher,
        container.settings.password_policy,
        container.clock,
    )
    created = await use_case.execute(
        CreateUserCommand(
            actor_user_id=str(claims.user_id), email=str(body.email), password=body.password
        )
    )
    return schemas.CreateUserResponse(user_id=created.user_id, email=created.email)


@router.get("/users/{user_id}", response_model=schemas.UserDetailResponse)
async def get_user(
    user_id: str,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> schemas.UserDetailResponse:
    use_case = GetUser(container.platform_uow_factory, container.clock)
    detail = await use_case.execute(
        GetUserQuery(actor_user_id=str(claims.user_id), target_user_id=user_id)
    )
    return schemas.UserDetailResponse(
        user=_user_summary(detail.user),
        platform_roles=detail.platform_roles,
        platform_permissions=detail.platform_permissions,
        memberships=[
            schemas.UserMembershipResponse(
                membership_id=m.membership_id,
                tenant_id=m.tenant_id,
                tenant_slug=m.tenant_slug,
                tenant_display_name=m.tenant_display_name,
                status=m.status,
                is_default=m.is_default,
                job_title=m.job_title,
            )
            for m in detail.memberships
        ],
    )


@router.patch("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def update_user(
    user_id: str,
    body: schemas.UpdateUserRequest,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> None:
    use_case = UpdateUser(container.platform_uow_factory, container.clock)
    await use_case.execute(
        UpdateUserCommand(
            actor_user_id=str(claims.user_id), target_user_id=user_id, email=str(body.email)
        )
    )


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: str,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> None:
    use_case = DeleteUser(container.platform_uow_factory, container.clock)
    await use_case.execute(
        DeleteUserCommand(actor_user_id=str(claims.user_id), target_user_id=user_id)
    )


@router.post("/users/{user_id}/suspend", status_code=status.HTTP_204_NO_CONTENT)
async def suspend_user(
    user_id: str,
    body: schemas.SetUserStatusRequest,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> None:
    use_case = SetUserStatus(container.platform_uow_factory, container.clock)
    await use_case.execute(
        SetUserStatusCommand(
            actor_user_id=str(claims.user_id),
            target_user_id=user_id,
            suspend=True,
            reason=body.reason,
        )
    )


@router.post("/users/{user_id}/reactivate", status_code=status.HTTP_204_NO_CONTENT)
async def reactivate_user(
    user_id: str,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> None:
    use_case = SetUserStatus(container.platform_uow_factory, container.clock)
    await use_case.execute(
        SetUserStatusCommand(
            actor_user_id=str(claims.user_id), target_user_id=user_id, suspend=False
        )
    )


@router.get("/me/effective-permissions", response_model=schemas.EffectivePermissionsResponse)
async def get_my_effective_platform_permissions(
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> schemas.EffectivePermissionsResponse:
    use_case = ResolvePlatformEffectivePermissions(container.platform_uow_factory, container.clock)
    permissions = await use_case.execute(
        ResolvePlatformEffectivePermissionsQuery(user_id=str(claims.user_id))
    )
    return schemas.EffectivePermissionsResponse(permissions=sorted(permissions))


def _platform_model_configuration_response(
    item: ModelConfigurationWithAccess,
    usage: list[schemas.TenantTokenUsageResponse] | None = None,
) -> schemas.PlatformModelConfigurationResponse:
    configuration = item.configuration
    return schemas.PlatformModelConfigurationResponse(
        id=configuration.id,
        model_name=configuration.model_name,
        parameters=configuration.parameters,
        token_budget_per_month=configuration.token_budget_per_month,
        provider_credential_id=configuration.provider_credential_id,
        owning_tenant_id=configuration.tenant_id,
        archived_at=configuration.archived_at,
        tenant_ids=item.tenant_ids,
        tenant_usage=usage or [],
        created_at=configuration.created_at,
    )


async def _tenant_usage(
    container: AppContainer, item: ModelConfigurationWithAccess
) -> list[schemas.TenantTokenUsageResponse]:
    """This month's spend for each tenant granted this configuration.

    **Degrades instead of failing, unlike the enforcement path**, and the
    asymmetry is deliberate: refusing to *spend* on an unconfirmable budget
    protects someone's bill, but refusing to *render a page* over the same
    unavailable counter protects nothing and takes the console down with
    Redis. An unreadable figure is reported as `None` and shown as unknown.
    """
    results: list[schemas.TenantTokenUsageResponse] = []
    for tenant_id in item.tenant_ids:
        try:
            used: int | None = await container.token_usage.read(
                tenant_id=tenant_id, model_configuration_id=item.configuration.id
            )
        except Exception:
            logger.warning(
                "token usage unavailable for tenant %s / configuration %s",
                tenant_id,
                item.configuration.id,
            )
            used = None
        results.append(
            schemas.TenantTokenUsageResponse(
                tenant_id=tenant_id, tokens_used_this_month=used
            )
        )
    return results


@router.get("/overview", response_model=schemas.PlatformOverviewResponse)
async def get_platform_overview(
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> schemas.PlatformOverviewResponse:
    """Spend across every provider and tenant, for the operator dashboard.

    Computed live -- see the note in `platform_overview.py` on why, and on what
    the fix is when the tenant count makes that stop being reasonable.
    """
    use_case = GetPlatformOverview(
        container.platform_uow_factory,
        container.clock,
        container.token_usage,
        container.tenant_quota,
    )
    overview = await use_case.execute(PlatformOverviewQuery(actor_user_id=str(claims.user_id)))
    return schemas.PlatformOverviewResponse(
        providers=[
            schemas.ProviderSpendResponse(
                provider=p.provider,
                model_count=p.model_count,
                total_tokens=p.total_tokens,
                used_tokens=p.used_tokens,
                remaining_tokens=p.remaining_tokens,
                running_low=p.running_low,
                has_unbudgeted=p.has_unbudgeted,
            )
            for p in overview.providers
        ],
        tenants=[
            schemas.TenantSpendResponse(
                tenant_id=t.tenant_id,
                slug=t.slug,
                display_name=t.display_name,
                max_tokens_per_month=t.max_tokens_per_month,
                used_tokens=t.used_tokens,
                remaining_tokens=t.remaining_tokens,
                running_low=t.running_low,
                max_messages_per_day=t.max_messages_per_day,
                effective_messages_per_day=t.effective_messages_per_day,
                used_messages_today=t.used_messages_today,
                remaining_messages_today=t.remaining_messages_today,
                token_alert_level=t.token_alert_level,
                message_alert_level=t.message_alert_level,
                input_tokens=t.input_tokens,
                output_tokens=t.output_tokens,
                ingestion_tokens=t.ingestion_tokens,
                cost_usd=t.cost_usd,
                unpriced_tokens=t.unpriced_tokens,
                models=[
                    schemas.TenantModelSpendResponse(
                        model_configuration_id=m.model_configuration_id,
                        model_name=m.model_name,
                        provider=m.provider,
                        token_budget_per_month=m.token_budget_per_month,
                        used_tokens=m.used_tokens,
                    )
                    for m in t.models
                ],
            )
            for t in overview.tenants
        ],
        tenants_running_low=overview.tenants_running_low,
        unattributed_tokens=overview.unattributed_tokens,
        ingestion_tokens=overview.ingestion_tokens,
        costs=schemas.CostSummaryResponse(
            total_usd=overview.costs.total_usd,
            priced_tokens=overview.costs.priced_tokens,
            unpriced_tokens=overview.costs.unpriced_tokens,
            by_model=[
                schemas.ModelCostResponse(
                    model=m.model,
                    kind=m.kind,
                    tokens=m.tokens,
                    unpriced_tokens=m.unpriced_tokens,
                    cost_usd=m.cost_usd,
                )
                for m in overview.costs.by_model
            ],
        ),
        low_remaining_fraction=LOW_REMAINING_FRACTION,
    )


@router.get("/activity", response_model=schemas.PlatformActivityResponse)
async def get_platform_activity(
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> schemas.PlatformActivityResponse:
    """Activity, satisfaction, queues, ingestion and health for the dashboard.

    Counts only -- see `platform_activity.py` -- so, unlike the feedback
    review, reading it is not audited. The permission check runs first; the
    health probe only after it, so an unauthorised caller learns nothing about
    the deployment's dependencies.
    """
    activity = await GetPlatformActivity(container.platform_uow_factory, container.clock).execute(
        PlatformActivityQuery(actor_user_id=str(claims.user_id))
    )
    # Never raises by contract -- a failed probe is a result, not an error.
    report = await container.health_check.check()

    def comparison(c: PeriodComparison) -> schemas.PeriodComparisonResponse:
        return schemas.PeriodComparisonResponse(current=c.current, previous=c.previous)

    return schemas.PlatformActivityResponse(
        generated_at=activity.generated_at,
        daily=[
            schemas.DailyActivityResponse(
                day=d.day,
                conversations_started=d.conversations_started,
                questions=d.questions,
                answers=d.answers,
                handoffs=d.handoffs,
                tokens=d.tokens,
            )
            for d in activity.daily
        ],
        trend_days=TREND_DAYS,
        questions=comparison(activity.questions),
        conversations=comparison(activity.conversations),
        handoffs=comparison(activity.handoffs),
        satisfaction_days=SATISFACTION_DAYS,
        helpful=comparison(activity.helpful),
        not_helpful=comparison(activity.not_helpful),
        active_tenants=activity.active_tenants,
        waiting_handoffs=activity.waiting_handoffs,
        handled_handoffs=activity.handled_handoffs,
        oldest_waiting_at=activity.oldest_waiting_at,
        documents_processing=activity.documents_processing,
        documents_stuck=activity.documents_stuck,
        documents_failed=activity.documents_failed,
        stuck_after_minutes=int(STUCK_AFTER.total_seconds() // 60),
        attention=[
            schemas.TenantAttentionResponse(
                tenant_id=a.tenant_id,
                display_name=a.display_name,
                slug=a.slug,
                waiting_handoffs=a.waiting_handoffs,
                oldest_waiting_at=a.oldest_waiting_at,
                stuck_documents=a.stuck_documents,
                failed_documents=a.failed_documents,
            )
            for a in activity.attention
        ],
        usage_recorded_since=activity.usage_recorded_since,
        health=[
            schemas.DependencyHealthResponse(name=d.name, healthy=d.healthy)
            for d in report.dependencies
        ],
    )


def _price_response(p: ModelPrice) -> schemas.ModelPriceResponse:
    return schemas.ModelPriceResponse(
        id=p.id,
        model_name=p.model_name,
        input_usd_per_million=p.input_usd_per_million,
        output_usd_per_million=p.output_usd_per_million,
        effective_from=p.effective_from,
        created_at=p.created_at,
    )


@router.get("/model-prices", response_model=schemas.ModelPriceCatalogueResponse)
async def list_model_prices(
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> schemas.ModelPriceCatalogueResponse:
    """The price list, and the models this month's usage needs priced."""
    catalogue = await ListModelPrices(container.platform_uow_factory, container.clock).execute(
        ListModelPricesQuery(actor_user_id=str(claims.user_id))
    )
    return schemas.ModelPriceCatalogueResponse(
        prices=[_price_response(p) for p in catalogue.prices],
        models_in_use=[
            schemas.ModelInUseResponse(
                model=m.model,
                kind=m.kind,
                tokens_this_month=m.tokens_this_month,
                current=_price_response(m.current) if m.current else None,
            )
            for m in catalogue.models_in_use
        ],
    )


@router.post(
    "/model-prices",
    status_code=status.HTTP_201_CREATED,
    response_model=schemas.CreateModelPriceResponse,
)
async def set_model_price(
    body: schemas.SetModelPriceRequest,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> schemas.CreateModelPriceResponse:
    """Adds a price entry. Entries are never edited: a change is a new entry."""
    price_id = await SetModelPrice(container.platform_uow_factory, container.clock).execute(
        SetModelPriceCommand(
            actor_user_id=str(claims.user_id),
            model_name=body.model_name,
            input_usd_per_million=body.input_usd_per_million,
            output_usd_per_million=body.output_usd_per_million,
            effective_from=body.effective_from,
        )
    )
    return schemas.CreateModelPriceResponse(id=price_id)


@router.delete("/model-prices/{price_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_model_price(
    price_id: UUID,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> Response:
    """Removes a mistaken entry; the values it held are kept in the audit log."""
    await DeleteModelPrice(container.platform_uow_factory, container.clock).execute(
        DeleteModelPriceCommand(actor_user_id=str(claims.user_id), price_id=str(price_id))
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/model-configurations",
    response_model=schemas.PlatformModelConfigurationListResponse,
)
async def list_platform_model_configurations(
    include_archived: bool = True,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> schemas.PlatformModelConfigurationListResponse:
    """The catalogue, with the tenants each entry is available to."""
    use_case = ListModelConfigurationsForPlatform(container.platform_uow_factory, container.clock)
    items = await use_case.execute(
        ListModelConfigurationsForPlatformQuery(
            actor_user_id=str(claims.user_id), include_archived=include_archived
        )
    )
    return schemas.PlatformModelConfigurationListResponse(
        model_configurations=[
            _platform_model_configuration_response(i, await _tenant_usage(container, i))
            for i in items
        ]
    )


@router.post(
    "/model-configurations",
    status_code=status.HTTP_201_CREATED,
    response_model=schemas.CreateModelConfigurationResponse,
)
async def create_model_configuration(
    body: schemas.CreateModelConfigurationRequest,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> schemas.CreateModelConfigurationResponse:
    """Adds a platform-owned model. Creating it grants it to nobody."""
    use_case = CreateModelConfiguration(container.platform_uow_factory, container.clock)
    configuration_id = await use_case.execute(
        CreateModelConfigurationCommand(
            actor_user_id=str(claims.user_id),
            model_name=body.model_name,
            parameters=body.parameters,
            token_budget_per_month=body.token_budget_per_month,
            provider_credential_id=(
                str(body.provider_credential_id) if body.provider_credential_id else None
            ),
        )
    )
    return schemas.CreateModelConfigurationResponse(id=configuration_id)


@router.patch(
    "/model-configurations/{model_configuration_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def update_model_configuration(
    model_configuration_id: str,
    body: schemas.UpdateModelConfigurationRequest,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> Response:
    use_case = UpdateModelConfiguration(container.platform_uow_factory, container.clock)
    await use_case.execute(
        UpdateModelConfigurationCommand(
            actor_user_id=str(claims.user_id),
            model_configuration_id=model_configuration_id,
            model_name=body.model_name,
            parameters=body.parameters,
            token_budget_per_month=body.token_budget_per_month,
            provider_credential_id=(
                str(body.provider_credential_id) if body.provider_credential_id else None
            ),
        )
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/model-configurations/{model_configuration_id}/archive",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def archive_model_configuration(
    model_configuration_id: str,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> Response:
    """Withdraws it from new assignments. Existing assistants keep working."""
    use_case = ArchiveModelConfiguration(container.platform_uow_factory, container.clock)
    await use_case.execute(
        ModelConfigurationActionCommand(
            actor_user_id=str(claims.user_id),
            model_configuration_id=model_configuration_id,
        )
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/model-configurations/{model_configuration_id}/restore",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def restore_model_configuration(
    model_configuration_id: str,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> Response:
    use_case = RestoreModelConfiguration(container.platform_uow_factory, container.clock)
    await use_case.execute(
        ModelConfigurationActionCommand(
            actor_user_id=str(claims.user_id),
            model_configuration_id=model_configuration_id,
        )
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/model-configurations/{model_configuration_id}/tenants",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def grant_model_configuration(
    model_configuration_id: str,
    body: schemas.GrantModelConfigurationRequest,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> Response:
    """Makes this configuration available to one tenant. Idempotent."""
    use_case = GrantModelConfigurationToTenant(container.platform_uow_factory, container.clock)
    await use_case.execute(
        TenantAccessCommand(
            actor_user_id=str(claims.user_id),
            model_configuration_id=model_configuration_id,
            tenant_id=str(body.tenant_id),
        )
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/model-configurations/{model_configuration_id}/tenants/{tenant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_model_configuration(
    model_configuration_id: str,
    tenant_id: str,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> Response:
    """Refused with a 409 while any of that tenant's assistants still use it."""
    use_case = RevokeModelConfigurationFromTenant(container.platform_uow_factory, container.clock)
    await use_case.execute(
        TenantAccessCommand(
            actor_user_id=str(claims.user_id),
            model_configuration_id=model_configuration_id,
            tenant_id=tenant_id,
        )
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- tenant entitlements ----------------------------------------------------
#
# The platform's lever over a tenant. Gated on the same permission as the model
# catalogue: both are the platform deciding what a tenant may spend the
# platform's money on, and a permission per screen produces a catalogue nobody
# can reason about.


@router.get("/tenant-entitlements", response_model=schemas.TenantEntitlementsListResponse)
async def list_tenant_entitlements(
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> schemas.TenantEntitlementsListResponse:
    use_case = ListTenantEntitlements(container.platform_uow_factory, container.clock)
    items = await use_case.execute(
        ListTenantEntitlementsQuery(actor_user_id=str(claims.user_id))
    )
    return schemas.TenantEntitlementsListResponse(
        entitlements=[_entitlements_response(e) for e in items]
    )


@router.put(
    "/tenants/{tenant_id}/entitlements",
    response_model=schemas.TenantEntitlementsResponse,
)
async def set_tenant_entitlements(
    tenant_id: UUID,
    body: schemas.TenantEntitlementsRequest,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> schemas.TenantEntitlementsResponse:
    """Sets a tenant's whole plan in one write.

    PUT rather than PATCH: the eight fields are one policy decision, and a
    partial update would let an operator raise a token cap while believing
    they had also tightened a capability flag they never sent.
    """
    use_case = SetTenantEntitlements(container.platform_uow_factory, container.clock)
    entitlements = await use_case.execute(
        SetTenantEntitlementsCommand(
            actor_user_id=str(claims.user_id),
            tenant_id=str(tenant_id),
            max_knowledge_bases=body.max_knowledge_bases,
            max_chat_widgets=body.max_chat_widgets,
            max_messages_per_day=body.max_messages_per_day,
            max_tokens_per_month=body.max_tokens_per_month,
            allow_invite_members=body.allow_invite_members,
            allow_create_roles=body.allow_create_roles,
        )
    )
    return _entitlements_response(entitlements)


@router.get("/ai-providers", response_model=schemas.ProviderCapabilityListResponse)
async def list_ai_providers(
    claims: AccessTokenClaims = Depends(get_current_claims),
) -> schemas.ProviderCapabilityListResponse:
    """The provider catalogue and what each supports.

    Static data, so no use case and no unit of work -- but still behind
    authentication, because it tells a reader which providers this deployment
    can talk to, which is deployment topology rather than public information.
    """
    del claims
    return schemas.ProviderCapabilityListResponse(
        providers=[
            schemas.ProviderCapabilityResponse(
                provider=c.provider.value,
                label=c.label,
                supported=c.supported,
                supports_embeddings=c.supports_embeddings,
                supports_embedding_dimensions=c.supports_embedding_dimensions,
                supports_reasoning_effort=c.supports_reasoning_effort,
                supports_request_timeout=c.supports_request_timeout,
            )
            for c in all_capabilities()
        ]
    )


def _entitlements_response(
    entitlements: TenantEntitlements,
) -> schemas.TenantEntitlementsResponse:
    return schemas.TenantEntitlementsResponse(
        tenant_id=entitlements.tenant_id,
        max_knowledge_bases=entitlements.max_knowledge_bases,
        max_chat_widgets=entitlements.max_chat_widgets,
        max_messages_per_day=entitlements.max_messages_per_day,
        max_tokens_per_month=entitlements.max_tokens_per_month,
        allow_invite_members=entitlements.allow_invite_members,
        allow_create_roles=entitlements.allow_create_roles,
        updated_at=entitlements.updated_at,
    )


@router.get("/answer-feedback", response_model=schemas.PlatformFeedbackResponse)
async def list_platform_answer_feedback(
    tenant_id: str | None = None,
    rating: str | None = None,
    channel: str | None = None,
    limit: int = 25,
    offset: int = 0,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> schemas.PlatformFeedbackResponse:
    """Answer ratings across every tenant. Each read is audited: it returns
    tenants' conversation content to someone outside the tenant."""
    from iam_platform.application.ai_resources.platform_feedback import (
        ListPlatformAnswerFeedback,
        PlatformFeedbackQuery,
    )

    page = await ListPlatformAnswerFeedback(
        container.platform_uow_factory, container.clock
    ).execute(
        PlatformFeedbackQuery(
            actor_user_id=str(claims.user_id),
            tenant_id=tenant_id,
            rating=rating,
            channel=channel,
            limit=limit,
            offset=offset,
        )
    )
    return schemas.PlatformFeedbackResponse(
        items=[
            schemas.PlatformFeedbackItem(
                id=r.id,
                tenant_id=r.tenant_id,
                tenant_name=r.tenant_name,
                rating=r.rating,
                question=r.question,
                answer=r.answer,
                comment=r.comment,
                channel=r.channel,
                knowledge_base_name=r.knowledge_base_name,
                author_email=r.author_email,
                created_at=r.created_at,
            )
            for r in page.items
        ],
        total=page.total,
        by_tenant=[
            schemas.PlatformFeedbackTenantSummary(
                tenant_id=s.tenant_id,
                tenant_name=s.tenant_name,
                total=s.total,
                helpful=s.helpful,
                not_helpful=s.not_helpful,
            )
            for s in page.by_tenant
        ],
    )
