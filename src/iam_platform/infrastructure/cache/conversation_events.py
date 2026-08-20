"""Realtime fan-out for the Unassigned inbox, over Redis pub/sub + SSE.

**Why not WebSockets.** This platform has no WebSocket layer, and the traffic
here is one-way: the server tells consoles that a conversation is waiting.
Server-Sent Events already carry streamed answers through the console's BFF
proxy, are already proven to pass through it unbuffered, and reconnect on their
own. Adding a WebSocket stack would mean a second realtime transport to
authenticate, secure, scale and operate for a direction SSE already covers.

**Why Redis pub/sub rather than an in-process queue.** The API runs as several
processes behind a load balancer. An agent's SSE stream is held by whichever
one accepted it, and the handoff that should notify them is committed by a
different one. An in-memory fan-out would deliver to whoever happened to share
a process -- correct in development, silently broken in production, and only
under load.

**The channel is per tenant, and that is the isolation boundary.** A subscriber
is bound to the tenant resolved from their authenticated session, never to one
they supply, so a payload published for tenant A has no channel that tenant B
is listening on. Payloads deliberately carry ids and timestamps only, never
message content: an inbox notice does not need to say what a visitor asked, and
sending it would spray conversation text through a cache with a different
retention story from the database.

Pub/sub is fire-and-forget: an agent whose stream drops misses events that fire
while they are away. That is acceptable and deliberate -- the inbox is *also*
fetched on connect, so a reconnecting console resynchronises from the database.
The stream is an accelerator, not the source of truth.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from typing import Any
from uuid import UUID

from redis.asyncio import Redis

logger = logging.getLogger("iam_platform.infrastructure.cache.conversation_events")


def _channel(tenant_id: UUID) -> str:
    return f"conversation-events:{tenant_id}"


class RedisConversationEventPublisher:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def publish(
        self, *, tenant_id: UUID, event: str, payload: dict[str, Any]
    ) -> None:
        """Fails open. A missed notification degrades the inbox to its
        on-connect fetch; raising would fail a handoff that has already
        committed, which is strictly worse -- the visitor is waiting for a
        human either way, and only one of those outcomes tells them so."""
        try:
            await self._redis.publish(
                _channel(tenant_id), json.dumps({"event": event, **payload})
            )
        except Exception:
            logger.warning(
                "could not publish %s for tenant %s -- inboxes will resync on reconnect",
                event,
                tenant_id,
            )

    async def subscribe(self, *, tenant_id: UUID) -> AsyncGenerator[dict[str, Any]]:
        """Yields this tenant's events until the caller stops consuming."""
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(_channel(tenant_id))
        try:
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                try:
                    yield json.loads(message["data"])
                except (ValueError, TypeError):
                    # A malformed payload is dropped rather than raised: one
                    # bad message must not end an agent's whole stream.
                    logger.warning("dropping unparseable conversation event")
        finally:
            # Both, and in this order: unsubscribe stops delivery, close
            # releases the connection back to the pool. Skipping the second
            # leaks a connection per disconnected agent, which is the shape of
            # the leak the Phase 9 lifespan work had to fix elsewhere.
            await pubsub.unsubscribe(_channel(tenant_id))
            await pubsub.aclose()


class NullConversationEventPublisher:
    """No realtime. Publishing is a no-op and subscribing ends immediately.

    Not a degraded mode: the inbox still works, because it fetches on load.
    Used where no Redis client is wired, and by tests that are not about
    realtime.
    """

    def __init__(self) -> None:
        self.published: list[tuple[UUID, str, dict[str, Any]]] = []

    async def publish(
        self, *, tenant_id: UUID, event: str, payload: dict[str, Any]
    ) -> None:
        self.published.append((tenant_id, event, payload))

    async def subscribe(self, *, tenant_id: UUID) -> AsyncGenerator[dict[str, Any]]:
        del tenant_id
        return
        yield {}  # pragma: no cover -- makes this an async generator
