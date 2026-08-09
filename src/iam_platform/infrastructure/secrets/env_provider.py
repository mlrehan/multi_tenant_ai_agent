"""Development default ``SecretProvider`` -- resolves ``secret://<key>`` references
straight from the process environment. Cloud provider adapters (AWS Secrets
Manager, Vault, Azure Key Vault, GCP Secret Manager) are added when the
project is actually deployed to one of those environments (Phase 9) rather
than built speculatively now -- docs/21-configuration-and-secrets.md.
"""

from __future__ import annotations

import os

from iam_platform.core.errors import SecretNotFoundError


class EnvSecretProvider:
    async def get_secret(self, key: str) -> str:
        value = os.environ.get(key)
        if value is None:
            raise SecretNotFoundError(key)
        return value
