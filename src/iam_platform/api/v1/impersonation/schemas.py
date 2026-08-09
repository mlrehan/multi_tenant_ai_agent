from __future__ import annotations

from pydantic import BaseModel


class StartImpersonationRequest(BaseModel):
    tenant_id: str
    target_user_id: str
    reason: str


class StartImpersonationResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int


class EndImpersonationRequest(BaseModel):
    impersonation_session_id: str
