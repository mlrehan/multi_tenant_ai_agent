"""TOTP MFA via ``pyotp``."""

from __future__ import annotations

import pyotp

from iam_platform.core.config import MfaSettings


class PyOtpTotpService:
    def __init__(self, settings: MfaSettings) -> None:
        self._issuer = settings.totp_issuer

    def generate_secret(self) -> str:
        return pyotp.random_base32()

    def provisioning_uri(self, *, secret: str, account_email: str) -> str:
        return pyotp.TOTP(secret).provisioning_uri(name=account_email, issuer_name=self._issuer)

    def verify(self, *, secret: str, code: str) -> bool:
        # valid_window=1 tolerates +/-1 30s step of clock drift on the user's
        # authenticator app, a standard TOTP UX allowance.
        return pyotp.TOTP(secret).verify(code, valid_window=1)
