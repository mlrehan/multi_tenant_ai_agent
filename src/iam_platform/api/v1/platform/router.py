"""``/v1/platform/*`` -- platform-scope tenant lifecycle and role management.

No route-level permission dependency: each use case enforces its own
required permission internally (via ``compute_effective_platform_state``),
which keeps the use cases safe to call from anywhere (a worker, a script)
without depending on the API's dependency chain, and avoids computing
effective permissions twice per request now that there's no cache in front
of that computation (Phase 6 scope note, CLAUDE.md).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status

from iam_platform.api.deps.authn import get_container, get_current_claims
from iam_platform.api.deps.container import AppContainer
from iam_platform.api.v1.platform import schemas
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
        created_at=configuration.created_at,
    )


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
        model_configurations=[_platform_model_configuration_response(i) for i in items]
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
