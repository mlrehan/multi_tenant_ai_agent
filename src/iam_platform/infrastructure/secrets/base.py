"""``SecretProvider`` port -- docs/21-configuration-and-secrets.md."""

from __future__ import annotations

from typing import Protocol


class SecretProvider(Protocol):
    async def get_secret(self, key: str) -> str: ...
