"""Request/response DTOs for the auth router -- distinct from domain entities
per docs/19-folder-structure.md."""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class VerifyEmailRequest(BaseModel):
    token: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int


class LoginResponse(BaseModel):
    status: str
    tokens: TokenResponse | None = None
    mfa_challenge_id: str | None = None


class MfaVerifyRequest(BaseModel):
    challenge_id: str
    code: str


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class RequestPasswordResetRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=1, max_length=256)


class TotpEnrollStartResponse(BaseModel):
    mfa_method_id: str
    secret: str
    provisioning_uri: str


class TotpEnrollConfirmRequest(BaseModel):
    mfa_method_id: str
    code: str


class OAuthStartResponse(BaseModel):
    authorization_url: str


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class MfaMethodResponse(BaseModel):
    id: str
    type: str
    label: str | None
    is_primary: bool
    verified: bool
    created_at: str
    last_used_at: str | None


class LinkedProviderResponse(BaseModel):
    provider: str
    provider_email: str | None
    linked_at: str


class AccountResponse(BaseModel):
    """The caller's own identity. Carries no secret material by construction --
    see `GetMyAccount`'s docstring."""

    user_id: str
    email: str
    status: str
    email_verified: bool
    created_at: str
    last_login_at: str | None
    has_password: bool
    mfa_methods: list[MfaMethodResponse]
    linked_providers: list[LinkedProviderResponse]
