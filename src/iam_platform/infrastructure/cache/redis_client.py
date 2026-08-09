"""Redis client construction -- docs/14 cache/background-jobs design."""

from __future__ import annotations

from redis.asyncio import Redis

from iam_platform.core.config import RedisSettings


def build_redis_client(settings: RedisSettings) -> Redis:
    # `Redis.from_url` is annotated as returning Any by redis-py, so the cast
    # is what gives callers a real type instead of silently propagating Any
    # through the container. (mypy >= 2 flags the bare return under
    # `no-any-return`; the annotation was always the honest intent.)
    client: Redis = Redis.from_url(settings.url.get_secret_value(), decode_responses=True)
    return client
