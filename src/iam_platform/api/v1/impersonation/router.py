"""``/v1/platform/impersonation/*`` -- docs/06-authorization-model.md §5."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status

from iam_platform.api.deps.authn import get_container, get_current_claims
from iam_platform.api.deps.container import AppContainer
from iam_platform.api.v1.impersonation import schemas
from iam_platform.application.identity.ports import AccessTokenClaims
from iam_platform.application.impersonation.end_impersonation import (
    EndImpersonation,
    EndImpersonationCommand,
)
from iam_platform.application.impersonation.start_impersonation import (
    StartImpersonation,
    StartImpersonationCommand,
)

router = APIRouter(prefix="/v1/platform/impersonation", tags=["impersonation"])


@router.post("/start", response_model=schemas.StartImpersonationResponse)
async def start_impersonation(
    body: schemas.StartImpersonationRequest,
    request: Request,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> schemas.StartImpersonationResponse:
    use_case = StartImpersonation(container.platform_uow_factory, container.jwt_issuer, container.clock)
    issued = await use_case.execute(
        StartImpersonationCommand(
            platform_user_id=str(claims.user_id),
            tenant_id=body.tenant_id,
            target_user_id=body.target_user_id,
            reason=body.reason,
            ip=request.client.host if request.client else None,
        )
    )
    return schemas.StartImpersonationResponse(
        access_token=issued.token,
        expires_in=container.settings.jwt.access_token_ttl_seconds,
    )


@router.post("/end", status_code=status.HTTP_204_NO_CONTENT)
async def end_impersonation(
    body: schemas.EndImpersonationRequest,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> None:
    use_case = EndImpersonation(container.platform_uow_factory, container.clock)
    await use_case.execute(
        EndImpersonationCommand(
            platform_user_id=str(claims.user_id),
            impersonation_session_id=body.impersonation_session_id,
        )
    )
