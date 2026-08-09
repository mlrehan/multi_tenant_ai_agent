"""Server-side OAuth state/nonce/PKCE storage -- docs/05-authentication-flows.md OIDC flow."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime

from redis.asyncio import Redis

from iam_platform.core.security_tokens import generate_opaque_token

_KEY_PREFIX = "oauth_state:"


class RedisOAuthStateStore:
    def __init__(self, redis: Redis, *, ttl_seconds: int = 600) -> None:
        self._redis = redis
        self._ttl_seconds = ttl_seconds

    async def create(self, *, provider: str, now: datetime) -> tuple[str, str, str, str]:
        state = generate_opaque_token(num_bytes=24)
        nonce = generate_opaque_token(num_bytes=24)
        code_verifier = generate_opaque_token(num_bytes=48)
        digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
        code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

        payload = json.dumps({"provider": provider, "nonce": nonce, "code_verifier": code_verifier})
        await self._redis.set(f"{_KEY_PREFIX}{state}", payload, ex=self._ttl_seconds)
        return state, nonce, code_verifier, code_challenge

    async def consume(self, *, state: str) -> tuple[str, str, str] | None:
        key = f"{_KEY_PREFIX}{state}"
        raw = await self._redis.get(key)
        if raw is None:
            return None
        await self._redis.delete(key)  # single-use, per PKCE/state best practice
        data = json.loads(raw)
        return data["provider"], data["nonce"], data["code_verifier"]
