"""Maps application/domain exceptions to HTTP responses.

Centralized here rather than try/except in every route handler, so a new use
case automatically gets sane error responses as long as it raises from the
existing exception hierarchies.
"""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from iam_platform.application.ai_resources import exceptions as ai_resource_errors
from iam_platform.application.identity import exceptions as identity_errors
from iam_platform.application.impersonation import exceptions as impersonation_errors
from iam_platform.application.platform_authz import exceptions as platform_authz_errors
from iam_platform.application.tenancy import exceptions as tenancy_errors
from iam_platform.application.tenant_authz import exceptions as tenant_authz_errors
from iam_platform.domain.shared.exceptions import DomainError

# Several modules define same-named exceptions (RoleNotFoundError,
# SelfEscalationError, PermissionDeniedError, MembershipNotFoundError, ...)
# for their own scope -- imported via module aliases above rather than
# symbol-level `as` aliases, since a status map keyed by ~15 individually
# renamed symbols across four modules was harder to read than `module.Name`.

_IDENTITY_STATUS_MAP: dict[type[identity_errors.ApplicationError], int] = {
    identity_errors.WeakPasswordError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    identity_errors.InvalidCredentialsError: status.HTTP_401_UNAUTHORIZED,
    identity_errors.AccountLockedError: status.HTTP_423_LOCKED,
    identity_errors.RateLimitExceededError: status.HTTP_429_TOO_MANY_REQUESTS,
    identity_errors.InvalidOrExpiredTokenError: status.HTTP_400_BAD_REQUEST,
    identity_errors.RefreshReuseDetectedError: status.HTTP_401_UNAUTHORIZED,
    identity_errors.MfaChallengeInvalidError: status.HTTP_400_BAD_REQUEST,
    identity_errors.MfaCodeInvalidError: status.HTTP_401_UNAUTHORIZED,
    identity_errors.NoUsableMfaMethodError: status.HTTP_400_BAD_REQUEST,
    identity_errors.CannotUnlinkLastAuthMethodError: status.HTTP_409_CONFLICT,
    identity_errors.OAuthAccountAlreadyLinkedError: status.HTTP_409_CONFLICT,
    identity_errors.OAuthEmailConflictError: status.HTTP_409_CONFLICT,
    identity_errors.UserNotFoundError: status.HTTP_404_NOT_FOUND,
}

_PLATFORM_AUTHZ_STATUS_MAP: dict[type[platform_authz_errors.PlatformAuthzError], int] = {
    platform_authz_errors.RoleNotFoundError: status.HTTP_404_NOT_FOUND,
    platform_authz_errors.PlatformPermissionNotFoundError: status.HTTP_404_NOT_FOUND,
    platform_authz_errors.UserNotFoundError: status.HTTP_404_NOT_FOUND,
    platform_authz_errors.TenantNotFoundError: status.HTTP_404_NOT_FOUND,
    platform_authz_errors.SelfEscalationError: status.HTTP_403_FORBIDDEN,
    platform_authz_errors.TenantCreationDeniedError: status.HTTP_403_FORBIDDEN,
    platform_authz_errors.UserManagementDeniedError: status.HTTP_403_FORBIDDEN,
    platform_authz_errors.TenantListDeniedError: status.HTTP_403_FORBIDDEN,
    platform_authz_errors.DuplicateSlugError: status.HTTP_409_CONFLICT,
    platform_authz_errors.DuplicateEmailError: status.HTTP_409_CONFLICT,
    platform_authz_errors.WeakPasswordError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    platform_authz_errors.DuplicatePlatformRoleCodeError: status.HTTP_409_CONFLICT,
    platform_authz_errors.SystemPlatformRoleImmutableError: status.HTTP_409_CONFLICT,
    platform_authz_errors.TenantOwnerRoleNotSeededError: status.HTTP_503_SERVICE_UNAVAILABLE,
}

_TENANCY_STATUS_MAP: dict[type[tenancy_errors.TenancyError], int] = {
    tenancy_errors.TenantNotFoundError: status.HTTP_404_NOT_FOUND,
    tenancy_errors.MembershipNotFoundError: status.HTTP_404_NOT_FOUND,
    tenancy_errors.InvalidOrExpiredInvitationError: status.HTTP_400_BAD_REQUEST,
    tenancy_errors.InvitationEmailMismatchError: status.HTTP_403_FORBIDDEN,
    tenancy_errors.PermissionDeniedError: status.HTTP_403_FORBIDDEN,
    tenancy_errors.MembershipAlreadyExistsError: status.HTTP_409_CONFLICT,
}

_TENANT_AUTHZ_STATUS_MAP: dict[type[tenant_authz_errors.TenantAuthzError], int] = {
    tenant_authz_errors.RoleNotFoundError: status.HTTP_404_NOT_FOUND,
    tenant_authz_errors.MembershipNotFoundError: status.HTTP_404_NOT_FOUND,
    tenant_authz_errors.PermissionNotFoundError: status.HTTP_404_NOT_FOUND,
    tenant_authz_errors.SelfEscalationError: status.HTTP_403_FORBIDDEN,
    tenant_authz_errors.PermissionDeniedError: status.HTTP_403_FORBIDDEN,
    tenant_authz_errors.DuplicateRoleCodeError: status.HTTP_409_CONFLICT,
    tenant_authz_errors.PermissionNotTenantCustomizableError: status.HTTP_409_CONFLICT,
    tenant_authz_errors.SystemRoleImmutableError: status.HTTP_409_CONFLICT,
}

_AI_RESOURCE_STATUS_MAP: dict[type[ai_resource_errors.AiResourceError], int] = {
    # Every *NotFoundError here doubles as "you may not see this" -- the use
    # cases raise it rather than a 403 when the caller fails the visibility
    # check, so a resource they can't reach is indistinguishable from one that
    # doesn't exist (docs/03-threat-model.md, existence-inference prevention).
    ai_resource_errors.AssistantNotFoundError: status.HTTP_404_NOT_FOUND,
    ai_resource_errors.KnowledgeBaseNotFoundError: status.HTTP_404_NOT_FOUND,
    ai_resource_errors.DocumentNotFoundError: status.HTTP_404_NOT_FOUND,
    ai_resource_errors.DataSourceNotFoundError: status.HTTP_404_NOT_FOUND,
    ai_resource_errors.ConversationNotFoundError: status.HTTP_404_NOT_FOUND,
    ai_resource_errors.ModelConfigurationNotFoundError: status.HTTP_404_NOT_FOUND,
    ai_resource_errors.ModelConfigurationManagementDeniedError: status.HTTP_403_FORBIDDEN,
    ai_resource_errors.ModelConfigurationInUseError: status.HTTP_409_CONFLICT,
    ai_resource_errors.TokenBudgetExceededError: status.HTTP_429_TOO_MANY_REQUESTS,
    ai_resource_errors.ProviderCredentialNotFoundError: status.HTTP_404_NOT_FOUND,
    ai_resource_errors.ProviderCredentialUnusableError: status.HTTP_409_CONFLICT,
    ai_resource_errors.PermissionDeniedError: status.HTTP_403_FORBIDDEN,
    ai_resource_errors.ResourceAccessDeniedError: status.HTTP_403_FORBIDDEN,
    # Deliberately 500, not 404: the row exists and storage lost the bytes.
    # See the exception's docstring -- telling a caller "not found" would
    # misattribute a platform failure to their own data.
    ai_resource_errors.DocumentContentNotFoundError: status.HTTP_500_INTERNAL_SERVER_ERROR,
    # Both are the caller's file being unreadable/unsupported, not a server
    # fault -- 422 rather than 400 because the request was well-formed and the
    # *content* is what couldn't be processed.
    ai_resource_errors.DocumentParseError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    ai_resource_errors.UnsupportedDocumentTypeError: status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    ai_resource_errors.DocumentTooLargeError: status.HTTP_413_CONTENT_TOO_LARGE,
    # The tenant supplied something invalid -- too many URLs, or a target this
    # platform refuses to fetch. A 400 with the reason, not a 500.
    ai_resource_errors.TooManyUrlsError: status.HTTP_400_BAD_REQUEST,
    ai_resource_errors.QuestionTooLongError: status.HTTP_400_BAD_REQUEST,
    # 404 rather than 403: an anonymous caller must not learn whether a public
    # key they guessed is real. Same "not provable to exist" rule the
    # authenticated surface applies to invisible resources.
    ai_resource_errors.WidgetUnavailableError: status.HTTP_404_NOT_FOUND,
    ai_resource_errors.WidgetOriginNotAllowedError: status.HTTP_403_FORBIDDEN,
    ai_resource_errors.WidgetQuotaExceededError: status.HTTP_429_TOO_MANY_REQUESTS,
    ai_resource_errors.ChatWidgetNotFoundError: status.HTTP_404_NOT_FOUND,
    ai_resource_errors.UnsafeCrawlTargetError: status.HTTP_400_BAD_REQUEST,
}

_IMPERSONATION_STATUS_MAP: dict[type[impersonation_errors.ImpersonationError], int] = {
    impersonation_errors.ImpersonationDeniedError: status.HTTP_403_FORBIDDEN,
    impersonation_errors.ImpersonationTargetNotFoundError: status.HTTP_404_NOT_FOUND,
    impersonation_errors.ImpersonationSessionNotFoundError: status.HTTP_404_NOT_FOUND,
}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(identity_errors.ApplicationError)
    async def handle_application_error(
        request: Request, exc: identity_errors.ApplicationError
    ) -> JSONResponse:
        status_code = _IDENTITY_STATUS_MAP.get(type(exc), status.HTTP_400_BAD_REQUEST)
        body: dict[str, object] = {"detail": str(exc) or type(exc).__name__}
        if isinstance(exc, identity_errors.WeakPasswordError):
            body["violations"] = exc.violations
        return JSONResponse(status_code=status_code, content=body)

    @app.exception_handler(platform_authz_errors.PlatformAuthzError)
    async def handle_platform_authz_error(
        request: Request, exc: platform_authz_errors.PlatformAuthzError
    ) -> JSONResponse:
        status_code = _PLATFORM_AUTHZ_STATUS_MAP.get(type(exc), status.HTTP_400_BAD_REQUEST)
        body: dict[str, object] = {"detail": str(exc) or type(exc).__name__}
        # Same shape as the identity handler's, so the admin console can render
        # policy failures against the field rather than as a generic toast.
        if isinstance(exc, platform_authz_errors.WeakPasswordError):
            body["violations"] = exc.violations
        return JSONResponse(status_code=status_code, content=body)

    @app.exception_handler(tenancy_errors.TenancyError)
    async def handle_tenancy_error(request: Request, exc: tenancy_errors.TenancyError) -> JSONResponse:
        status_code = _TENANCY_STATUS_MAP.get(type(exc), status.HTTP_400_BAD_REQUEST)
        return JSONResponse(status_code=status_code, content={"detail": str(exc) or type(exc).__name__})

    @app.exception_handler(tenant_authz_errors.TenantAuthzError)
    async def handle_tenant_authz_error(
        request: Request, exc: tenant_authz_errors.TenantAuthzError
    ) -> JSONResponse:
        status_code = _TENANT_AUTHZ_STATUS_MAP.get(type(exc), status.HTTP_400_BAD_REQUEST)
        return JSONResponse(status_code=status_code, content={"detail": str(exc) or type(exc).__name__})

    @app.exception_handler(ai_resource_errors.AiResourceError)
    async def handle_ai_resource_error(
        request: Request, exc: ai_resource_errors.AiResourceError
    ) -> JSONResponse:
        status_code = _AI_RESOURCE_STATUS_MAP.get(type(exc), status.HTTP_400_BAD_REQUEST)
        return JSONResponse(status_code=status_code, content={"detail": str(exc) or type(exc).__name__})

    @app.exception_handler(impersonation_errors.ImpersonationError)
    async def handle_impersonation_error(
        request: Request, exc: impersonation_errors.ImpersonationError
    ) -> JSONResponse:
        status_code = _IMPERSONATION_STATUS_MAP.get(type(exc), status.HTTP_400_BAD_REQUEST)
        return JSONResponse(status_code=status_code, content={"detail": str(exc) or type(exc).__name__})

    @app.exception_handler(DomainError)
    async def handle_domain_error(request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST, content={"detail": str(exc)}
        )
