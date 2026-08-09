"""Application-layer errors for the identity module.

These map to HTTP responses in ``api/exception_handlers.py``. Distinct from
``domain.shared.exceptions.DomainError`` (violated invariants) -- these
represent a use case failing for a business reason (bad credentials, expired
token) rather than an entity being asked to do something illegal.
"""

from __future__ import annotations


class ApplicationError(Exception):
    pass


class WeakPasswordError(ApplicationError):
    def __init__(self, violations: list[str]) -> None:
        super().__init__("password does not meet policy requirements")
        self.violations = violations


class InvalidCredentialsError(ApplicationError):
    pass


class AccountLockedError(ApplicationError):
    def __init__(self, *, unlock_at: str | None = None) -> None:
        super().__init__("account is temporarily locked")
        self.unlock_at = unlock_at


class RateLimitExceededError(ApplicationError):
    pass


class InvalidOrExpiredTokenError(ApplicationError):
    """Raised for expired/used/unknown email-verification, password-reset,
    refresh, or OAuth-state tokens."""


class RefreshReuseDetectedError(ApplicationError):
    """The presented refresh token was already rotated -- its whole family has
    been revoked. Callers must force the user to re-authenticate."""


class MfaChallengeInvalidError(ApplicationError):
    pass


class MfaCodeInvalidError(ApplicationError):
    pass


class NoUsableMfaMethodError(ApplicationError):
    pass


class CannotUnlinkLastAuthMethodError(ApplicationError):
    pass


class OAuthAccountAlreadyLinkedError(ApplicationError):
    """The OAuth subject is already linked to a different local account."""


class OAuthEmailConflictError(ApplicationError):
    """The OAuth profile's email matches an existing local account that has not
    linked this provider -- never auto-merge (docs/05-authentication-flows.md)."""


class UserNotFoundError(ApplicationError):
    """The user id in a valid access token has no row.

    Only reachable if the account was hard-deleted while a token was still
    live, so it maps to 404 rather than 401 -- the token itself is fine."""
