"""Data transfer objects returned by identity use cases.

Kept separate from ``domain`` entities so the ``api`` layer never needs to
reach into entity internals to build a response body.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal


@dataclass(frozen=True, slots=True)
class TokenPair:
    access_token: str
    refresh_token: str
    token_type: Literal["Bearer"] = "Bearer"
    expires_in: int = 0


class LoginStatus(StrEnum):
    SUCCESS = "success"
    MFA_REQUIRED = "mfa_required"


@dataclass(frozen=True, slots=True)
class LoginResult:
    status: LoginStatus
    tokens: TokenPair | None = None
    mfa_challenge_id: str | None = None


@dataclass(frozen=True, slots=True)
class TotpEnrollmentStarted:
    mfa_method_id: str
    secret: str
    provisioning_uri: str
