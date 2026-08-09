"""Fixed-window rate limiting via Redis INCR/EXPIRE -- docs/15 login throttling."""

from __future__ import annotations

from redis.asyncio import Redis


class RedisRateLimiter:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def check_and_increment(self, key: str, *, limit: int, window_seconds: int) -> bool:
        pipe = self._redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, window_seconds, nx=True)  # only set TTL on the first hit in the window
        count, _ = await pipe.execute()
        return int(count) <= limit
