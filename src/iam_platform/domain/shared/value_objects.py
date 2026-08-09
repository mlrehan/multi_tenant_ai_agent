"""Shared value objects.

Only ``Email`` is needed for the identity module (Phase 5). Other
cross-domain value objects named in docs/19-folder-structure.md
(``TenantSlug``, ``PermissionCode``, ...) are added alongside the domain
modules that first need them, rather than speculatively defined now.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from iam_platform.domain.shared.exceptions import InvariantViolationError

# Deliberately simple (not the full RFC 5322 grammar): reject obviously
# malformed input at the domain boundary. Real deliverability is verified
# out-of-band by the email_verifications flow, not by this regex.
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True, slots=True)
class Email:
    value: str

    def __post_init__(self) -> None:
        normalized = self.value.strip().lower()
        if not _EMAIL_PATTERN.match(normalized):
            raise InvariantViolationError(f"invalid email address: {self.value!r}")
        object.__setattr__(self, "value", normalized)

    def __str__(self) -> str:
        return self.value
