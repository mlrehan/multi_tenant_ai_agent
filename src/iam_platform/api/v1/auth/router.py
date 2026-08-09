"""``/v1/auth/*`` -- registration, login, MFA, refresh, logout, password reset, OAuth.

Every handler is a thin adapter: extract request data, build the relevant
``application`` use case from the container, call it, map the result to a
response schema. No business logic lives here.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from iam_platform.api.deps.authn import get_container, get_current_claims
from iam_platform.api.deps.container import AppContainer
from iam_platform.api.v1.auth import schemas
from iam_platform.application.identity.account import (
    ChangeMyPassword,
    ChangeMyPasswordCommand,
    GetMyAccount,
    GetMyAccountQuery,
)
from iam_platform.application.identity.dto import LoginResult
from iam_platform.application.identity.login_user import LoginCommand, LoginUser
from iam_platform.application.identity.logout import (
    Logout,
    LogoutAllDevices,
    LogoutAllDevicesCommand,
    LogoutCommand,
)
from iam_platform.application.identity.mfa import (
    ConfirmTotpEnrollment,
    ConfirmTotpEnrollmentCommand,
    StartTotpEnrollment,
    StartTotpEnrollmentCommand,
    VerifyMfaChallenge,
    VerifyMfaChallengeCommand,
)
from iam_platform.application.identity.oauth_login import (
    CompleteOAuthLogin,
    CompleteOAuthLoginCommand,
)
from iam_platform.application.identity.password_reset import (
    RequestPasswordReset,
    RequestPasswordResetCommand,
    ResetPassword,
    ResetPasswordCommand,
)
from iam_platform.application.identity.ports import AccessTokenClaims
from iam_platform.application.identity.refresh_session import (
    RefreshSession,
    RefreshSessionCommand,
)
from iam_platform.application.identity.register_user import (
    RegisterUser,
    RegisterUserCommand,
    VerifyEmail,
    VerifyEmailCommand,
)

router = APIRouter(prefix="/v1/auth", tags=["auth"])


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _user_agent(request: Request) -> str | None:
    return request.headers.get("user-agent")


def _login_response(result: LoginResult) -> schemas.LoginResponse:
    tokens = (
        schemas.TokenResponse(
            access_token=result.tokens.access_token,
            refresh_token=result.tokens.refresh_token,
            expires_in=result.tokens.expires_in,
        )
        if result.tokens is not None
        else None
    )
    return schemas.LoginResponse(
        status=result.status.value, tokens=tokens, mfa_challenge_id=result.mfa_challenge_id
    )


@router.post("/register", status_code=status.HTTP_202_ACCEPTED)
async def register(
    body: schemas.RegisterRequest,
    request: Request,
    container: AppContainer = Depends(get_container),
) -> dict[str, str]:
    use_case = RegisterUser(
        container.uow_factory,
        container.password_hasher,
        container.email_sender,
        container.settings.password_policy,
        container.clock,
    )
    await use_case.execute(
        RegisterUserCommand(
            email=body.email,
            password=body.password,
            ip=_client_ip(request),
            user_agent=_user_agent(request),
        )
    )
    return {"detail": "if the email is valid, a verification link has been sent"}


@router.get("/verify-email", status_code=status.HTTP_200_OK)
async def verify_email(
    token: str, container: AppContainer = Depends(get_container)
) -> dict[str, str]:
    use_case = VerifyEmail(container.uow_factory, container.clock)
    await use_case.execute(VerifyEmailCommand(token=token))
    return {"detail": "email verified"}


@router.post("/login", response_model=schemas.LoginResponse)
async def login(
    body: schemas.LoginRequest,
    request: Request,
    container: AppContainer = Depends(get_container),
) -> schemas.LoginResponse:
    use_case = LoginUser(
        container.uow_factory,
        container.password_hasher,
        container.jwt_issuer,
        container.mfa_challenge_store,
        container.rate_limiter,
        container.settings.lockout,
        container.clock,
        container.settings.jwt.access_token_ttl_seconds,
    )
    result = await use_case.execute(
        LoginCommand(
            email=body.email,
            password=body.password,
            ip=_client_ip(request),
            user_agent=_user_agent(request),
        )
    )
    return _login_response(result)


@router.post("/mfa/verify", response_model=schemas.LoginResponse)
async def verify_mfa(
    body: schemas.MfaVerifyRequest,
    request: Request,
    container: AppContainer = Depends(get_container),
) -> schemas.LoginResponse:
    use_case = VerifyMfaChallenge(
        container.uow_factory,
        container.jwt_issuer,
        container.totp_service,
        container.mfa_challenge_store,
        container.clock,
        container.settings.jwt.access_token_ttl_seconds,
    )
    result = await use_case.execute(
        VerifyMfaChallengeCommand(
            challenge_id=body.challenge_id,
            code=body.code,
            ip=_client_ip(request),
            user_agent=_user_agent(request),
        )
    )
    return _login_response(result)


@router.post("/refresh", response_model=schemas.TokenResponse)
async def refresh(
    body: schemas.RefreshRequest,
    request: Request,
    container: AppContainer = Depends(get_container),
) -> schemas.TokenResponse:
    use_case = RefreshSession(
        container.uow_factory,
        container.jwt_issuer,
        container.clock,
        container.settings.jwt.access_token_ttl_seconds,
    )
    tokens = await use_case.execute(
        RefreshSessionCommand(
            refresh_token=body.refresh_token,
            ip=_client_ip(request),
            user_agent=_user_agent(request),
        )
    )
    return schemas.TokenResponse(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        expires_in=tokens.expires_in,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    body: schemas.LogoutRequest, container: AppContainer = Depends(get_container)
) -> None:
    use_case = Logout(container.uow_factory, container.clock)
    await use_case.execute(LogoutCommand(refresh_token=body.refresh_token))


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> None:
    use_case = LogoutAllDevices(container.uow_factory, container.clock)
    await use_case.execute(LogoutAllDevicesCommand(user_id=str(claims.user_id)))


@router.post("/password-reset/request", status_code=status.HTTP_202_ACCEPTED)
async def request_password_reset(
    body: schemas.RequestPasswordResetRequest,
    request: Request,
    container: AppContainer = Depends(get_container),
) -> dict[str, str]:
    use_case = RequestPasswordReset(container.uow_factory, container.email_sender, container.clock)
    await use_case.execute(
        RequestPasswordResetCommand(
            email=body.email, ip=_client_ip(request), user_agent=_user_agent(request)
        )
    )
    return {"detail": "if the email is valid, a reset link has been sent"}


@router.post("/password-reset/confirm", status_code=status.HTTP_200_OK)
async def confirm_password_reset(
    body: schemas.ResetPasswordRequest, container: AppContainer = Depends(get_container)
) -> dict[str, str]:
    use_case = ResetPassword(
        container.uow_factory,
        container.password_hasher,
        container.settings.password_policy,
        container.clock,
    )
    await use_case.execute(ResetPasswordCommand(token=body.token, new_password=body.new_password))
    return {"detail": "password reset -- all other sessions have been signed out"}


@router.get("/me", response_model=schemas.AccountResponse)
async def get_my_account(
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> schemas.AccountResponse:
    use_case = GetMyAccount(container.uow_factory)
    profile = await use_case.execute(GetMyAccountQuery(user_id=str(claims.user_id)))
    return schemas.AccountResponse(
        user_id=profile.user_id,
        email=profile.email,
        status=profile.status,
        email_verified=profile.email_verified,
        created_at=profile.created_at.isoformat(),
        last_login_at=profile.last_login_at.isoformat() if profile.last_login_at else None,
        has_password=profile.has_password,
        mfa_methods=[
            schemas.MfaMethodResponse(
                id=m.id,
                type=m.type,
                label=m.label,
                is_primary=m.is_primary,
                verified=m.verified,
                created_at=m.created_at.isoformat(),
                last_used_at=m.last_used_at.isoformat() if m.last_used_at else None,
            )
            for m in profile.mfa_methods
        ],
        linked_providers=[
            schemas.LinkedProviderResponse(
                provider=p.provider,
                provider_email=p.provider_email,
                linked_at=p.linked_at.isoformat(),
            )
            for p in profile.linked_providers
        ],
    )


@router.post("/password/change", status_code=status.HTTP_200_OK)
async def change_my_password(
    body: schemas.ChangePasswordRequest,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> dict[str, str]:
    use_case = ChangeMyPassword(
        container.uow_factory,
        container.password_hasher,
        container.settings.password_policy,
        container.clock,
    )
    await use_case.execute(
        ChangeMyPasswordCommand(
            user_id=str(claims.user_id),
            current_password=body.current_password,
            new_password=body.new_password,
        )
    )
    return {"detail": "password changed -- all sessions have been signed out"}


@router.post("/mfa/totp/start", response_model=schemas.TotpEnrollStartResponse)
async def start_totp_enrollment(
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> schemas.TotpEnrollStartResponse:
    use_case = StartTotpEnrollment(container.uow_factory, container.totp_service, container.clock)
    result = await use_case.execute(StartTotpEnrollmentCommand(user_id=str(claims.user_id)))
    return schemas.TotpEnrollStartResponse(
        mfa_method_id=result.mfa_method_id,
        secret=result.secret,
        provisioning_uri=result.provisioning_uri,
    )


@router.post("/mfa/totp/confirm", status_code=status.HTTP_200_OK)
async def confirm_totp_enrollment(
    body: schemas.TotpEnrollConfirmRequest,
    claims: AccessTokenClaims = Depends(get_current_claims),
    container: AppContainer = Depends(get_container),
) -> dict[str, str]:
    use_case = ConfirmTotpEnrollment(container.uow_factory, container.totp_service, container.clock)
    await use_case.execute(
        ConfirmTotpEnrollmentCommand(
            user_id=str(claims.user_id), mfa_method_id=body.mfa_method_id, code=body.code
        )
    )
    return {"detail": "MFA enrolled"}


@router.get("/oauth/{provider}/start", response_model=schemas.OAuthStartResponse)
async def start_oauth(
    provider: str, container: AppContainer = Depends(get_container)
) -> schemas.OAuthStartResponse:
    oauth_provider = container.oauth_providers.get(provider)
    if oauth_provider is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown provider")

    state, nonce, _code_verifier, code_challenge = await container.oauth_state_store.create(
        provider=provider, now=container.clock.now()
    )
    url = oauth_provider.build_authorization_url(
        state=state, nonce=nonce, code_challenge=code_challenge
    )
    return schemas.OAuthStartResponse(authorization_url=url)


@router.get("/oauth/{provider}/callback", response_model=schemas.LoginResponse)
async def oauth_callback(
    provider: str,
    code: str,
    state: str,
    request: Request,
    container: AppContainer = Depends(get_container),
) -> schemas.LoginResponse:
    oauth_provider = container.oauth_providers.get(provider)
    if oauth_provider is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown provider")

    consumed = await container.oauth_state_store.consume(state=state)
    if consumed is None or consumed[0] != provider:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid or expired state")
    _stored_provider, nonce, code_verifier = consumed

    profile = await oauth_provider.exchange_code(
        code=code, code_verifier=code_verifier, expected_nonce=nonce
    )

    use_case = CompleteOAuthLogin(
        container.uow_factory,
        container.jwt_issuer,
        container.clock,
        container.settings.jwt.access_token_ttl_seconds,
    )
    result = await use_case.execute(
        CompleteOAuthLoginCommand(
            profile=profile,
            linking_user_id=None,
            ip=_client_ip(request),
            user_agent=_user_agent(request),
        )
    )
    assert result is not None  # linking_user_id=None always yields a LoginResult, never None
    return _login_response(result)
