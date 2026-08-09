"""Short-TTL pending-MFA challenge state -- docs/05-authentication-flows.md login+MFA flow."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from redis.asyncio import Redis

from iam_platform.core.security_tokens import generate_opaque_token

_KEY_PREFIX = "mfa_challenge:"


class RedisMfaChallengeStore:
    def __init__(self, redis: Redis, *, ttl_seconds: int = 300) -> None:
        self._redis = redis
        self._ttl_seconds = ttl_seconds

    async def create_challenge(self, *, user_id: UUID, now: datetime) -> str:
        challenge_id = generate_opaque_token(num_bytes=24)
        await self._redis.set(f"{_KEY_PREFIX}{challenge_id}", str(user_id), ex=self._ttl_seconds)
        return challenge_id

    async def get_user_id(self, challenge_id: str) -> UUID | None:
        value = await self._redis.get(f"{_KEY_PREFIX}{challenge_id}")
        if value is None:
            return None
        # The client is built with decode_responses=True (str values), but the
        # redis-py stubs type .get() as bytes | str | None regardless -- decode
        # defensively so this works even if a differently-configured client is
        # ever swapped in here.
        return UUID(value.decode() if isinstance(value, bytes) else value)

    async def consume(self, challenge_id: str) -> None:
        await self._redis.delete(f"{_KEY_PREFIX}{challenge_id}")
