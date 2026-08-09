"""``/v1/tenants/{tenant_id}/...`` -- effective permissions, custom roles,
role hierarchy, membership role assignment, authorization overrides.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from iam_platform.api.deps.authn import get_container, get_current_claims
from iam_platform.api.deps.container import AppContainer
from iam_platform.api.v1.rbac import schemas
from iam_platform.application.identity.ports import AccessTokenClaims
from iam_platform.application.tenant_authz.assign_membership_role import (
    AssignMembershipRole,
    AssignMembershipRoleCommand,
    RevokeMembershipRole,
    RevokeMembershipRoleCommand,
)
from iam_platform.application.tenant_authz.effective_permissions import (
    ResolveTenantEffectivePermissions,
    ResolveTenantEffectivePermissionsQuery,
)
from iam_platform.application.tenant_authz.list_catalog import (
    ListTenantPermissions,
    ListTenantRolePermissions,
    ListTenantRoles,
    TenantCatalogQuery,
)
from iam_platform.application.tenant_authz.manage_custom_role import (
    AddPermissionToRole,
    CreateCustomRole,
    CreateCustomRoleCommand,
    RemovePermissionFromRole,
    RolePermissionCommand,
)
from iam_platform.application.tenant_authz.manage_override import (
    CreateAuthorizationOverride,
    CreateAuthorizationOverrideCommand,
    RevokeAuthorizationOverride,
    RevokeAuthorizationOverrideCommand,
)
from iam_platform.application.tenant_authz.manage_role_hierarchy import (
    CreateRoleHierarchyEdge,
    CreateRoleHierarchyEdgeCommand,
)

router = APIRouter(prefix="/v1/tenants/{tenant_id}", tags=["rbac"])


@router.get("/roles", response_model=list[schemas.TenantRoleResponse])
async def list_tenant_roles(
    tenant_id: str,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> list[schemas.TenantRoleResponse]:
    use_case = ListTenantRoles(container.tenant_uow_factory, container.clock)
    roles = await use_case.execute(
        TenantCatalogQuery(actor_user_id=str(claims.user_id), tenant_id=tenant_id)
    )
    return [
        schemas.TenantRoleResponse(
            id=str(r.id),
            code=r.code,
            name=r.name,
            description=r.description,
            is_system=r.is_system,
            rank=r.rank,
        )
        for r in roles
    ]


@router.get("/permissions", response_model=list[schemas.TenantPermissionResponse])
async def list_tenant_permissions(
    tenant_id: str,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> list[schemas.TenantPermissionResponse]:
    use_case = ListTenantPermissions(container.tenant_uow_factory, container.clock)
    permissions = await use_case.execute(
        TenantCatalogQuery(actor_user_id=str(claims.user_id), tenant_id=tenant_id)
    )
    return [
        schemas.TenantPermissionResponse(
            code=p.code,
            resource=p.resource,
            action=p.action,
            description=p.description,
            risk_level=p.risk_level,
            is_system=p.is_system,
            tenant_customizable=p.tenant_customizable,
            required_feature=p.required_feature,
        )
        for p in permissions
    ]


@router.get("/roles/permissions", response_model=schemas.RolePermissionsResponse)
async def list_tenant_role_permissions(
    tenant_id: str,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> schemas.RolePermissionsResponse:
    use_case = ListTenantRolePermissions(container.tenant_uow_factory, container.clock)
    mapping = await use_case.execute(
        TenantCatalogQuery(actor_user_id=str(claims.user_id), tenant_id=tenant_id)
    )
    return schemas.RolePermissionsResponse(by_role_code=mapping.by_role_code)


@router.get("/me/effective-permissions", response_model=schemas.EffectivePermissionsResponse)
async def get_my_effective_tenant_permissions(
    tenant_id: str,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> schemas.EffectivePermissionsResponse:
    use_case = ResolveTenantEffectivePermissions(container.tenant_uow_factory, container.clock)
    permissions = await use_case.execute(
        ResolveTenantEffectivePermissionsQuery(tenant_id=tenant_id, user_id=str(claims.user_id))
    )
    return schemas.EffectivePermissionsResponse(permissions=sorted(permissions))


@router.post(
    "/roles", status_code=status.HTTP_201_CREATED, response_model=schemas.CreateCustomRoleResponse
)
async def create_custom_role(
    tenant_id: str,
    body: schemas.CreateCustomRoleRequest,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> schemas.CreateCustomRoleResponse:
    use_case = CreateCustomRole(container.tenant_uow_factory, container.clock)
    role_id = await use_case.execute(
        CreateCustomRoleCommand(
            actor_user_id=str(claims.user_id),
            tenant_id=tenant_id,
            code=body.code,
            name=body.name,
            description=body.description,
            rank=body.rank,
            permission_codes=body.permission_codes,
        )
    )
    return schemas.CreateCustomRoleResponse(role_id=str(role_id))


@router.post(
    "/roles/{role_code}/permissions/{permission_code}", status_code=status.HTTP_204_NO_CONTENT
)
async def add_permission_to_role(
    tenant_id: str,
    role_code: str,
    permission_code: str,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> None:
    use_case = AddPermissionToRole(container.tenant_uow_factory, container.clock)
    await use_case.execute(
        RolePermissionCommand(
            actor_user_id=str(claims.user_id),
            tenant_id=tenant_id,
            role_code=role_code,
            permission_code=permission_code,
        )
    )


@router.delete(
    "/roles/{role_code}/permissions/{permission_code}", status_code=status.HTTP_204_NO_CONTENT
)
async def remove_permission_from_role(
    tenant_id: str,
    role_code: str,
    permission_code: str,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> None:
    use_case = RemovePermissionFromRole(container.tenant_uow_factory, container.clock)
    await use_case.execute(
        RolePermissionCommand(
            actor_user_id=str(claims.user_id),
            tenant_id=tenant_id,
            role_code=role_code,
            permission_code=permission_code,
        )
    )


@router.post("/roles/hierarchy", status_code=status.HTTP_204_NO_CONTENT)
async def create_role_hierarchy_edge(
    tenant_id: str,
    body: schemas.CreateRoleHierarchyEdgeRequest,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> None:
    use_case = CreateRoleHierarchyEdge(container.tenant_uow_factory, container.clock)
    await use_case.execute(
        CreateRoleHierarchyEdgeCommand(
            actor_user_id=str(claims.user_id),
            tenant_id=tenant_id,
            parent_role_code=body.parent_role_code,
            child_role_code=body.child_role_code,
        )
    )


@router.post("/memberships/{membership_id}/roles", status_code=status.HTTP_204_NO_CONTENT)
async def assign_membership_role(
    tenant_id: str,
    membership_id: str,
    body: schemas.AssignRoleRequest,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> None:
    use_case = AssignMembershipRole(container.tenant_uow_factory, container.clock)
    await use_case.execute(
        AssignMembershipRoleCommand(
            actor_user_id=str(claims.user_id),
            tenant_id=tenant_id,
            target_membership_id=membership_id,
            role_code=body.role_code,
        )
    )


@router.delete("/memberships/{membership_id}/roles/{role_code}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_membership_role(
    tenant_id: str,
    membership_id: str,
    role_code: str,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> None:
    use_case = RevokeMembershipRole(container.tenant_uow_factory, container.clock)
    await use_case.execute(
        RevokeMembershipRoleCommand(
            actor_user_id=str(claims.user_id),
            tenant_id=tenant_id,
            target_membership_id=membership_id,
            role_code=role_code,
        )
    )


@router.post(
    "/overrides", status_code=status.HTTP_201_CREATED, response_model=schemas.CreateOverrideResponse
)
async def create_authorization_override(
    tenant_id: str,
    body: schemas.CreateOverrideRequest,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> schemas.CreateOverrideResponse:
    use_case = CreateAuthorizationOverride(container.tenant_uow_factory, container.clock)
    override_id = await use_case.execute(
        CreateAuthorizationOverrideCommand(
            actor_user_id=str(claims.user_id),
            tenant_id=tenant_id,
            target_membership_id=body.target_membership_id,
            permission_code=body.permission_code,
            effect=body.effect,
            reason=body.reason,
            expires_at=body.expires_at,
        )
    )
    return schemas.CreateOverrideResponse(override_id=str(override_id))


@router.delete("/overrides/{override_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_authorization_override(
    tenant_id: str,
    override_id: str,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> None:
    use_case = RevokeAuthorizationOverride(container.tenant_uow_factory, container.clock)
    await use_case.execute(
        RevokeAuthorizationOverrideCommand(
            actor_user_id=str(claims.user_id), tenant_id=tenant_id, override_id=override_id
        )
    )
