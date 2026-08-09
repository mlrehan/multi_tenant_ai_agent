"""Cross-cutting error hierarchy.

Distinct from ``domain.shared.exceptions.DomainError``: these are errors that
originate in ``core``/``infrastructure``/``application`` plumbing (config,
crypto, external services) rather than a violated business invariant. The
``api`` layer maps both hierarchies to HTTP responses, but they are kept
separate because ``domain`` is not allowed to import ``core`` (see
docs/20-dependency-rules.md) and therefore cannot subclass anything defined
here.
"""

from __future__ import annotations


class AppError(Exception):
    """Base class for all non-domain application errors."""


class ConfigurationError(AppError):
    """Raised when settings fail validation or a required secret is missing."""


class SecretNotFoundError(ConfigurationError):
    def __init__(self, key: str) -> None:
        super().__init__(f"secret not found: {key}")
        self.key = key


class TokenError(AppError):
    """Base class for JWT/refresh-token related failures."""


class TokenExpiredError(TokenError):
    pass


class TokenInvalidError(TokenError):
    pass
