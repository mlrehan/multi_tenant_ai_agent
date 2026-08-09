"""Opaque token generation/hashing -- stdlib only, no external dependency.

Used for refresh tokens, email-verification tokens, and password-reset
tokens: the raw value is returned to the caller exactly once and only its
hash is ever persisted (docs/05-authentication-flows.md). Kept in ``core``
rather than behind a port because ``secrets``/``hashlib`` behave identically
in every environment -- there's no alternate implementation a test would
ever need to substitute, unlike Argon2id hashing (configurable, external
library) or JWT signing (needs key material).
"""

from __future__ import annotations

import hashlib
import secrets


def generate_opaque_token(*, num_bytes: int = 32) -> str:
    return secrets.token_urlsafe(num_bytes)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
