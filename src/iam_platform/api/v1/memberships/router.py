"""``/v1/tenants/{tenant_id}/memberships/*`` -- membership lifecycle."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from iam_platform.api.deps.authn import get_container, get_current_claims
from iam_platform.api.deps.container import AppContainer
from iam_platform.api.v1.memberships import schemas
from iam_platform.application.identity.ports import AccessTokenClaims
from iam_platform.application.tenancy.invite_member import (
    AddMemberDirectly,
    AddMemberDirectlyCommand,
    UpdateMembership,
    UpdateMembershipCommand,
)
from iam_platform.application.tenancy.list_tenant_members import (
    ListMembershipRoles,
    ListMembershipRolesQuery,
)
from iam_platform.application.tenancy.manage_membership import (
    MembershipLifecycleCommand,
    ReactivateMembership,
    RestoreMembership,
    RevokeMembership,
    SuspendMembership,
)

router = APIRouter(prefix="/v1/tenants/{tenant_id}/memberships", tags=["memberships"])


@router.post("", status_code=status.HTTP_201_CREATED, response_model=schemas.AddMemberResponse)
async def add_member(
    tenant_id: str,
    body: schemas.AddMemberRequest,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> schemas.AddMemberResponse:
    use_case = AddMemberDirectly(container.tenant_uow_factory, container.clock)
    membership_id = await use_case.execute(
        AddMemberDirectlyCommand(
            actor_user_id=str(claims.user_id),
            tenant_id=tenant_id,
            target_user_id=body.user_id,
            role_codes=body.role_codes,
            job_title=body.job_title,
        )
    )
    return schemas.AddMemberResponse(membership_id=str(membership_id))


@router.patch("/{membership_id}", status_code=status.HTTP_204_NO_CONTENT)
async def update_membership(
    tenant_id: str,
    membership_id: str,
    body: schemas.UpdateMembershipRequest,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> None:
    use_case = UpdateMembership(container.tenant_uow_factory, container.clock)
    await use_case.execute(
        UpdateMembershipCommand(
            actor_user_id=str(claims.user_id),
            tenant_id=tenant_id,
            target_membership_id=membership_id,
            job_title=body.job_title,
        )
    )


@router.get("/{membership_id}/roles", response_model=list[schemas.MembershipRoleResponse])
async def list_membership_roles(
    tenant_id: str,
    membership_id: str,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> list[schemas.MembershipRoleResponse]:
    use_case = ListMembershipRoles(container.tenant_uow_factory, container.clock)
    assignments = await use_case.execute(
        ListMembershipRolesQuery(
            actor_user_id=str(claims.user_id), tenant_id=tenant_id, target_membership_id=membership_id
        )
    )
    return [
        schemas.MembershipRoleResponse(role_id=str(a.role_id), granted_at=a.granted_at.isoformat())
        for a in assignments
    ]


@router.post("/{membership_id}/suspend", status_code=status.HTTP_204_NO_CONTENT)
async def suspend_membership(
    tenant_id: str,
    membership_id: str,
    body: schemas.MembershipActionRequest,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> None:
    use_case = SuspendMembership(container.tenant_uow_factory, container.clock)
    await use_case.execute(
        MembershipLifecycleCommand(
            actor_user_id=str(claims.user_id),
            tenant_id=tenant_id,
            target_membership_id=membership_id,
            reason=body.reason,
        )
    )


@router.post("/{membership_id}/reactivate", status_code=status.HTTP_204_NO_CONTENT)
async def reactivate_membership(
    tenant_id: str,
    membership_id: str,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> None:
    use_case = ReactivateMembership(container.tenant_uow_factory, container.clock)
    await use_case.execute(
        MembershipLifecycleCommand(
            actor_user_id=str(claims.user_id), tenant_id=tenant_id, target_membership_id=membership_id
        )
    )


@router.post("/{membership_id}/revoke", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_membership(
    tenant_id: str,
    membership_id: str,
    body: schemas.MembershipActionRequest,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> None:
    use_case = RevokeMembership(container.tenant_uow_factory, container.clock)
    await use_case.execute(
        MembershipLifecycleCommand(
            actor_user_id=str(claims.user_id),
            tenant_id=tenant_id,
            target_membership_id=membership_id,
            reason=body.reason,
        )
    )


@router.post("/{membership_id}/restore", status_code=status.HTTP_204_NO_CONTENT)
async def restore_membership(
    tenant_id: str,
    membership_id: str,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> None:
    use_case = RestoreMembership(container.tenant_uow_factory, container.clock)
    await use_case.execute(
        MembershipLifecycleCommand(
            actor_user_id=str(claims.user_id), tenant_id=tenant_id, target_membership_id=membership_id
        )
    )
