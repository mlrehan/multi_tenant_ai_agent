"""Envelope encryption for provider credentials -- docs/16-schema-ai-resources.md.

Implements ``application.ai_resources.ports.CredentialEncryptor``.

**Scope note (Phase 7):** this is the *data-key* half of envelope encryption
only -- Fernet (AES-128-CBC + HMAC-SHA256, authenticated) under a single key
from settings. The production shape wraps that data key with a KMS-managed
key so the plaintext data key never sits in config, and rotates it per
credential. That KMS integration is deferred; what matters for this phase is
that the boundary exists and nothing outside it ever sees plaintext, so
adding KMS later changes this file and no caller.

Fernet rather than raw AES because it is authenticated by construction: a
tampered ciphertext fails to decrypt instead of silently yielding garbage
that some downstream provider call would then send over the wire.
"""

from __future__ import annotations

from cryptography.fernet import Fernet

_KEY_HINT_CHARS = 4


class FernetCredentialEncryptor:
    def __init__(self, data_key: str) -> None:
        # Raises immediately on a malformed key rather than at first use, so a
        # misconfigured deployment fails at startup, not on a user's request.
        self._fernet = Fernet(data_key.encode("utf-8"))

    def encrypt(self, plaintext: str) -> bytes:
        return self._fernet.encrypt(plaintext.encode("utf-8"))

    def decrypt(self, ciphertext: bytes) -> str:
        return self._fernet.decrypt(ciphertext).decode("utf-8")

    def key_hint(self, plaintext: str) -> str:
        """Last four characters -- the only part of a secret any UI may show.

        Short secrets are masked entirely rather than partially revealed: a
        4-character secret would otherwise be published in full by its own
        "hint".
        """
        if len(plaintext) <= _KEY_HINT_CHARS:
            return "*" * len(plaintext)
        return plaintext[-_KEY_HINT_CHARS:]
