"""AWS Secrets Manager ``SecretProvider`` -- docs/21-configuration-and-secrets.md.

``boto3`` is an optional dependency: this module imports it lazily inside the
constructor so a development or CI environment that never selects
``SECRET_PROVIDER=aws_secrets_manager`` doesn't need it installed. Importing at
module scope would make ``bootstrap.py`` -- which imports every provider to
build its selection map -- fail without boto3 present.

**Cache semantics.** A short in-memory TTL cache avoids a network round trip
per settings access. Secrets are read once at startup in the normal case, so
the cache mostly matters for a future hot-reload path; the TTL is what bounds
how long a rotated secret can remain stale in a running process.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from iam_platform.core.errors import SecretNotFoundError


class AwsSecretsManagerProvider:
    def __init__(
        self,
        *,
        region_name: str,
        cache_ttl_seconds: int = 300,
        client: Any | None = None,
    ) -> None:
        if client is not None:
            self._client = client
        else:  # pragma: no cover - requires AWS credentials to exercise
            try:
                import boto3
            except ImportError as exc:
                raise RuntimeError(
                    "SECRET_PROVIDER=aws_secrets_manager requires boto3; "
                    "install the 'aws' extra"
                ) from exc
            self._client = boto3.client("secretsmanager", region_name=region_name)

        self._cache_ttl_seconds = cache_ttl_seconds
        self._cache: dict[str, tuple[str, float]] = {}

    async def get_secret(self, key: str) -> str:
        cached = self._cache.get(key)
        if cached is not None:
            value, fetched_at = cached
            if time.monotonic() - fetched_at < self._cache_ttl_seconds:
                return value

        # boto3 is synchronous; run it off the event loop so a slow Secrets
        # Manager call can't block every other coroutine in the process.
        try:
            response = await asyncio.to_thread(
                self._client.get_secret_value, SecretId=key
            )
        except Exception as exc:
            # Deliberately not re-raising the boto3 error: its string form can
            # include the secret ARN and account ID, which routinely end up in
            # startup logs and crash reports.
            raise SecretNotFoundError(key) from exc

        raw = response.get("SecretString")
        if raw is None:
            raise SecretNotFoundError(key)

        value = str(raw)
        self._cache[key] = (value, time.monotonic())
        return value
