"""An idle SSE stream must survive its own keepalive.

The defect this guards against was invisible to every other test and to the
browser: `asyncio.wait_for(subscription.__anext__(), timeout=...)` cancels the
coroutine it is waiting on, and a `CancelledError` thrown into an async
generator suspended at an `await` *ends that generator*. So the first idle
keepalive killed the subscription; the next read raised `StopAsyncIteration`
and the endpoint returned, closing the stream.

Nothing complained. `EventSource` reconnects by itself, so an agent's inbox
appeared to work while every event published during the reconnect gap was lost
outright -- Redis pub/sub has no replay.

These tests use a stand-in subscription with the same shape as the real one
(an async generator that awaits between yields). The property under test is the
consumption pattern, not Redis.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress

import pytest

pytestmark = pytest.mark.unit


async def _subscription(queue: asyncio.Queue[dict[str, str]]) -> AsyncIterator[dict[str, str]]:
    """The shape of `ConversationEventPublisher.subscribe`: an async generator
    that suspends on an await between yields."""
    while True:
        yield await queue.get()


async def _consume_the_old_way(
    subscription: AsyncIterator[dict[str, str]], timeout: float
) -> list[object]:
    """The original endpoint loop, kept verbatim so the guard has something to
    fail against. Not used in production -- see `_consume_the_fixed_way`."""
    seen: list[object] = []
    while True:
        try:
            seen.append(await asyncio.wait_for(subscription.__anext__(), timeout))
        except TimeoutError:
            seen.append("keepalive")
            continue
        except StopAsyncIteration:
            seen.append("STREAM CLOSED")
            return seen
        return seen  # a real event arrived; that is all this needs to observe


async def _consume_the_fixed_way(
    subscription: AsyncIterator[dict[str, str]], timeout: float
) -> list[object]:
    """What the endpoint does now: a task drains the subscription into a queue
    and the timeout is applied to `queue.get()`, which is safe to cancel."""
    seen: list[object] = []
    relay: asyncio.Queue[dict[str, str] | None] = asyncio.Queue()

    async def drain() -> None:
        try:
            async for event in subscription:
                await relay.put(event)
        finally:
            await relay.put(None)

    pump = asyncio.create_task(drain())
    try:
        while True:
            try:
                event = await asyncio.wait_for(relay.get(), timeout)
            except TimeoutError:
                seen.append("keepalive")
                continue
            if event is None:
                seen.append("STREAM CLOSED")
                return seen
            seen.append(event)
            return seen  # a real event arrived after the keepalives
    finally:
        pump.cancel()
        with suppress(asyncio.CancelledError):
            await pump


class TestAnIdleKeepaliveMustNotEndTheStream:
    async def test_the_old_pattern_closes_the_stream_on_the_first_keepalive(
        self,
    ) -> None:
        """The bug, demonstrated. Without this the fix below is a claim rather
        than a repair -- a test that only exercises the new code cannot show
        the old code was broken."""
        source: asyncio.Queue[dict[str, str]] = asyncio.Queue()
        subscription = _subscription(source)

        async def publish_after_the_keepalive() -> None:
            await asyncio.sleep(0.09)
            await source.put({"event": "conversation.unassigned"})

        asyncio.create_task(publish_after_the_keepalive())
        seen = await _consume_the_old_way(subscription, timeout=0.03)

        assert seen[0] == "keepalive"
        assert seen[-1] == "STREAM CLOSED"
        # The event published while the stream was "open" never arrived.
        assert not any(isinstance(item, dict) for item in seen)

    async def test_the_fixed_pattern_keeps_delivering_after_keepalives(self) -> None:
        source: asyncio.Queue[dict[str, str]] = asyncio.Queue()
        subscription = _subscription(source)

        async def publish_after_the_keepalive() -> None:
            await asyncio.sleep(0.09)
            await source.put({"event": "conversation.unassigned"})

        asyncio.create_task(publish_after_the_keepalive())
        seen = await _consume_the_fixed_way(subscription, timeout=0.03)

        assert "keepalive" in seen, "the idle path should still emit keepalives"
        assert {"event": "conversation.unassigned"} in seen, (
            "an event published after an idle keepalive must still be delivered"
        )
        assert "STREAM CLOSED" not in seen
