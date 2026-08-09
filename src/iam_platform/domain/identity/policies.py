"""Pure policy functions for the identity domain -- no I/O, no framework types.

Parameters that would otherwise come from ``core.config.Settings`` are passed
in explicitly by the caller (an application-layer use case), since ``domain``
cannot import ``core`` (docs/20-dependency-rules.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True, slots=True)
class PasswordPolicyViolation:
    code: str
    message: str


def validate_password(
    password: str, *, min_length: int, max_length: int
) -> list[PasswordPolicyViolation]:
    """Returns an empty list if the password satisfies policy, else a list of violations.

    Deliberately does not check the password against the user's email/name --
    that requires context this pure function doesn't have and belongs in the
    calling use case if desired.
    """
    violations: list[PasswordPolicyViolation] = []

    if len(password) < min_length:
        violations.append(
            PasswordPolicyViolation("too_short", f"password must be at least {min_length} characters")
        )
    if len(password) > max_length:
        violations.append(
            PasswordPolicyViolation("too_long", f"password must be at most {max_length} characters")
        )

    class_count = sum(
        [
            any(c.islower() for c in password),
            any(c.isupper() for c in password),
            any(c.isdigit() for c in password),
            any(not c.isalnum() for c in password),
        ]
    )
    if class_count < 3:
        violations.append(
            PasswordPolicyViolation(
                "too_weak",
                "password must contain at least 3 of: lowercase, uppercase, digit, symbol",
            )
        )

    return violations


def should_lock_account(*, failed_attempt_count: int, max_failed_attempts: int) -> bool:
    return failed_attempt_count >= max_failed_attempts


def compute_lockout_expiry(*, now: datetime, lockout_minutes: int) -> datetime:
    return now + timedelta(minutes=lockout_minutes)


def is_locked(*, unlock_at: datetime | None, now: datetime) -> bool:
    return unlock_at is not None and now < unlock_at
