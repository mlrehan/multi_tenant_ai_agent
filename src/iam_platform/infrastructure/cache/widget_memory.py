"""A visitor's conversation, for the length of their session and no longer.

**Redis, not Postgres, and that is the whole design.** The authenticated side
stores turns in `conversation_messages` because a member owns their history:
they reopen it, search it, rename it, and a tenant admin may audit it. A
website visitor is none of those things -- `WidgetSessionClaims` carries no
user id, no membership and no permissions by construction, so there is nobody
for a stored thread to belong to and nobody with standing to delete it.

Three consequences follow, and each is a reason rather than a convenience:

* **It expires by itself.** The key's TTL is the session token's own lifetime,
  so memory and the right to use it end together. Persisting a stranger's
  questions past the session they asked them in is storing personal data with
  no owner, no retention policy and no deletion path -- and the platform would
  be doing it on the tenant's behalf without either party asking.
* **`conversations` would not take it anyway.** `membership_id` and
  `assistant_id` are both NOT NULL and both meaningless for a visitor. Making
  them nullable to fit an anonymous session would weaken a constraint that
  currently guarantees every stored conversation has a real owner.
* **Losing it is survivable.** Redis restarting costs a visitor their thread's
  context, which is a degraded answer -- not a lost record. That is why this
  store **fails open** where the quota store beside it fails closed: an
  unavailable counter must never become unlimited spending, but an unavailable
  memory must not take the whole widget down.
"""

from __future__ import annotations

import json
import logging
from uuid import UUID

from redis.asyncio import Redis

logger = logging.getLogger("iam_platform.infrastructure.cache.widget_memory")

#: Turns kept for a visitor. Deliberately shorter than the authenticated
#: window: a widget exchange is a support question and its follow-ups, not a
#: working session, and every stored turn is prompt budget spent on someone
#: who may have already closed the tab.
MAX_WIDGET_TURNS = 4


def _key(session_id: UUID) -> str:
    return f"widget:memory:{session_id}"


class RedisWidgetMemoryStore:
    """Recent turns for one widget session.

    Keyed by `session_id` from the token, never by widget or IP: two visitors
    on the same page hold different sessions and must not see each other's
    questions. The session id is unguessable and dies with the token.
    """

    def __init__(self, redis: Redis, *, ttl_seconds: int) -> None:
        self._redis = redis
        # Matches the session token's lifetime, so a key cannot outlive the
        # credential that is allowed to read it.
        self._ttl = ttl_seconds

    async def recent(self, session_id: UUID) -> list[tuple[str, str]]:
        """The session's turns as `(role, content)`, oldest first.

        **Fails open**: a memory that cannot be read degrades the answer to a
        context-free one, which is exactly what the visitor would have got
        before this existed. Refusing instead would take a working widget down
        over a cache blip.
        """
        try:
            raw = await self._redis.get(_key(session_id))
        except Exception:
            logger.warning("widget memory unavailable for session %s", session_id)
            return []
        if not raw:
            return []
        try:
            stored = json.loads(raw)
            return [(str(t["role"]), str(t["content"])) for t in stored][-MAX_WIDGET_TURNS:]
        except Exception:
            # Malformed is treated as absent rather than raised: the only way
            # this happens is a format change, and one visitor losing context
            # beats every widget failing during a rollout.
            logger.warning("widget memory for session %s could not be parsed", session_id)
            return []

    async def append(self, session_id: UUID, *, question: str, answer: str) -> None:
        """Adds one exchange, trimming to the most recent turns.

        Read-modify-write rather than a Redis list: the window is four entries,
        the value is one small JSON blob, and a per-session key has no
        concurrent writer worth a transaction -- a visitor asks one question at
        a time.

        Fails open for the same reason as `recent`, and with less at stake: the
        answer has already been delivered.
        """
        try:
            existing = await self.recent(session_id)
            turns = [
                *[{"role": r, "content": c} for r, c in existing],
                {"role": "user", "content": question},
                {"role": "assistant", "content": answer},
            ][-MAX_WIDGET_TURNS:]
            await self._redis.set(_key(session_id), json.dumps(turns), ex=self._ttl)
        except Exception:
            logger.warning("could not record widget memory for session %s", session_id)


class NullWidgetMemoryStore:
    """No memory. Every widget question answers standalone.

    Not a failure mode -- it is exactly how the widget behaved before this
    existed, so it is a safe stand-in wherever a Redis client is not wired.
    """

    async def recent(self, session_id: UUID) -> list[tuple[str, str]]:
        return []

    async def append(self, session_id: UUID, *, question: str, answer: str) -> None:
        return None
