"""Argon2id password hashing -- docs/15 (OWASP) via ``argon2-cffi``."""

from __future__ import annotations

from argon2 import PasswordHasher as Argon2PasswordHasher
from argon2 import exceptions as argon2_exceptions


class Argon2IdPasswordHasher:
    def __init__(self) -> None:
        # argon2-cffi's defaults (time_cost=3, memory_cost=65536 KiB, parallelism=4)
        # already match OWASP's current Argon2id recommendation; not overridden.
        self._hasher = Argon2PasswordHasher()

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, password: str, hashed: str) -> bool:
        try:
            self._hasher.verify(hashed, password)
        except (argon2_exceptions.VerificationError, argon2_exceptions.InvalidHashError):
            # VerificationError is the base class covering VerifyMismatchError
            # (decoded fine, password just didn't match) *and* decode-time
            # failures like "Decoding failed" (a malformed hash -- caught in
            # the wild via the login use case's intentionally-fake dummy
            # hash, before that constant was fixed to be validly encoded).
            # Any of these must mean "not authenticated," never propagate as
            # an unhandled 500 -- a caller providing attacker-controlled
            # input to `verify()` must never be able to crash the request.
            return False
        return True

    def needs_rehash(self, hashed: str) -> bool:
        return self._hasher.check_needs_rehash(hashed)
