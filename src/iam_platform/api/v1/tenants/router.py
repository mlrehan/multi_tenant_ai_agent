"""``/v1/tenants/*`` -- membership listing, invitations."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from iam_platform.api.deps.authn import get_container, get_current_claims
from iam_platform.api.deps.container import AppContainer
from iam_platform.api.v1.tenants import schemas
from iam_platform.application.identity.ports import AccessTokenClaims
from iam_platform.application.tenancy.invite_member import (
    AcceptInvitation,
    AcceptInvitationCommand,
    InviteMember,
    InviteMemberCommand,
)
from iam_platform.application.tenancy.list_memberships import (
    ListMyTenantMemberships,
    ListMyTenantMembershipsQuery,
)
from iam_platform.application.tenancy.list_tenant_members import (
    ListTenantMembers,
    ListTenantMembersQuery,
)

router = APIRouter(prefix="/v1/tenants", tags=["tenants"])


@router.get("/me/memberships", response_model=list[schemas.TenantMembershipResponse])
async def list_my_memberships(
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> list[schemas.TenantMembershipResponse]:
    use_case = ListMyTenantMemberships(container.tenant_uow_factory)
    memberships = await use_case.execute(ListMyTenantMembershipsQuery(user_id=str(claims.user_id)))
    return [
        schemas.TenantMembershipResponse(
            membership_id=str(m.id),
            tenant_id=str(m.tenant_id),
            status=m.status.value,
            is_default=m.is_default,
        )
        for m in memberships
    ]


@router.get("/{tenant_id}/memberships", response_model=list[schemas.TenantMemberResponse])
async def list_tenant_members(
    tenant_id: str,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> list[schemas.TenantMemberResponse]:
    use_case = ListTenantMembers(container.tenant_uow_factory, container.clock)
    members = await use_case.execute(
        ListTenantMembersQuery(actor_user_id=str(claims.user_id), tenant_id=tenant_id)
    )
    return [
        schemas.TenantMemberResponse(
            membership_id=str(m.id),
            user_id=str(m.user_id),
            status=m.status.value,
            is_default=m.is_default,
            department_id=str(m.department_id) if m.department_id else None,
            team_id=str(m.team_id) if m.team_id else None,
            job_title=m.job_title,
            created_at=m.created_at.isoformat(),
        )
        for m in members
    ]


@router.post(
    "/{tenant_id}/invitations", status_code=status.HTTP_202_ACCEPTED
)
async def invite_member(
    tenant_id: str,
    body: schemas.InviteMemberRequest,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> dict[str, str]:
    use_case = InviteMember(container.tenant_uow_factory, container.invitation_email_sender, container.clock)
    await use_case.execute(
        InviteMemberCommand(
            actor_user_id=str(claims.user_id),
            tenant_id=tenant_id,
            email=body.email,
            role_codes=body.role_codes,
        )
    )
    return {"detail": "invitation sent"}


@router.post("/{tenant_id}/invitations/accept", status_code=status.HTTP_200_OK)
async def accept_invitation(
    tenant_id: str,
    body: schemas.AcceptInvitationRequest,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> dict[str, str]:
    # AcceptInvitationCommand cross-checks the invitation's email against the
    # accepting user's own -- fetch it from the identity module (JWT claims
    # deliberately don't carry email, per docs/05-authentication-flows.md's
    # minimal-claims token design).
    async with container.uow_factory() as identity_uow:
        user = await identity_uow.users.get_by_id(claims.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")

    use_case = AcceptInvitation(container.tenant_uow_factory, container.clock)
    await use_case.execute(
        AcceptInvitationCommand(
            accepting_user_id=str(claims.user_id),
            accepting_user_email=str(user.email),
            tenant_id=tenant_id,
            token=body.token,
        )
    )
    return {"detail": "invitation accepted"}
