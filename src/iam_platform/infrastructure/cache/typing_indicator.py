"""Who is currently typing in a conversation.

**Redis with a short TTL, and never a message row.** A typing indicator is not
something either party said; it is a fact about the next few seconds. Writing
it to `conversation_messages` would put it in the transcript, in the retention
sweep, in the agent's audit view and in the prompt sent to the model -- four
places it does not belong, for a fact that is wrong again three seconds later.

**The TTL is the design, not a cleanup detail.** The hard case is not "stopped
typing", which the client reports; it is the browser that never reports
anything again -- a closed laptop, a dropped connection, a crashed tab. A flag
that is switched on and off would stick on forever in exactly that case, and
the other party would watch "Agent is typing..." for the rest of the session.
Instead the client re-asserts typing every few seconds and the key simply
lapses, so *every* way of leaving -- graceful or not -- ends the indicator.

**One key per side, not per person**, which is what keeps two agents on the
same conversation from producing two indicators. The visitor is told a
colleague is typing; which colleague, or how many, is not something the widget
should be reporting to a stranger anyway. The value carries a display name so
the tenant side can say who, and the visitor side deliberately ignores it.
"""

from __future__ import annotations

import logging

from redis.asyncio import Redis

logger = logging.getLogger("iam_platform.infrastructure.cache.typing_indicator")

#: How long an assertion of "I am typing" stands without renewal.
#:
#: The client refreshes every ~3 seconds, so this is comfortably more than one
#: heartbeat interval -- a single dropped request must not make a live typist
#: flicker -- and comfortably less than the pause that means someone has walked
#: away. Longer would leave a ghost indicator after a tab closes; shorter would
#: blink during an ordinary pause for thought.
TYPING_TTL_SECONDS = 8

#: The two sides. Not membership ids: the conversation has exactly two ends,
#: and modelling it as "whoever is typing on the tenant side" is what makes
#: multiple agents collapse to one indicator for free.
VISITOR = "visitor"
AGENT = "agent"


def _key(conversation_id: str, side: str) -> str:
    return f"typing:{conversation_id}:{side}"


class RedisTypingIndicatorStore:
    """Ephemeral typing state for both ends of a conversation.

    **Fails open in both directions, and neither failure is silent-but-costly.**
    An unreadable indicator shows nothing, which is what every chat looked like
    before this existed. An unwritable one means the other party is not told
    someone is composing a reply. Redis being down must not take a working
    conversation with it -- unlike the quota store beside it, where an
    unconfirmable count becomes unlimited spending.
    """

    def __init__(self, redis: Redis, *, ttl_seconds: int = TYPING_TTL_SECONDS) -> None:
        self._redis = redis
        self._ttl = ttl_seconds

    async def mark_typing(
        self, *, conversation_id: str, side: str, display_name: str = ""
    ) -> None:
        """Asserts (or renews) that this side is composing something.

        `SET` with an expiry rather than `SETEX` plus a separate refresh: one
        round trip, and the renewal and the first assertion are the same
        operation, so there is no "was it already set?" branch to get wrong.
        """
        try:
            await self._redis.set(
                _key(conversation_id, side), display_name or side, ex=self._ttl
            )
        except Exception:
            logger.warning(
                "typing indicator could not be recorded for conversation %s",
                conversation_id,
            )

    async def clear(self, *, conversation_id: str, side: str) -> None:
        """Ends the indicator now.

        Called when a message is sent or the box is emptied. The TTL would end
        it anyway, but several seconds later -- and an indicator still showing
        underneath a message that has already arrived reads as a second message
        coming that never does.
        """
        try:
            await self._redis.delete(_key(conversation_id, side))
        except Exception:
            logger.warning(
                "typing indicator could not be cleared for conversation %s",
                conversation_id,
            )

    async def who_is_typing(self, *, conversation_id: str, side: str) -> str | None:
        """The display name for that side, or `None` if nobody is typing."""
        try:
            raw = await self._redis.get(_key(conversation_id, side))
        except Exception:
            logger.warning(
                "typing indicator unavailable for conversation %s", conversation_id
            )
            return None
        if raw is None:
            return None
        return raw.decode() if isinstance(raw, bytes) else str(raw)
