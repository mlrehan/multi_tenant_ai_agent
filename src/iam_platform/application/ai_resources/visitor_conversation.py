"""The one way a visitor's conversation is created and appended to.

Two paths reach it -- an ordinary question (`AskWidget`) and an escalation
(`SelectHandoffTeam`) -- and both must produce the *same* row shape. The
`exactly_one_owner` CHECK requires `membership_id IS NULL` with
`visitor_session_id` set; a second creator that got that wrong would fail at
the database, and one that got it subtly right in a different way (a different
title, a different initial state) would leave the console showing two kinds of
visitor thread.

**Persisting on the first question, not on escalation.** Widget conversations
used to live only in Redis for the length of a session, and were written to
Postgres only if a colleague was asked for. That made ordinary widget traffic
unreadable afterwards: the tenant's Conversations screen showed nothing,
because nothing was there. They are now stored from the first exchange, and
bounded by `tenant_chatbot_settings.conversation_retention_days` -- storage
with an expiry date rather than storage forever, which is what makes keeping
an anonymous stranger's questions defensible at all.

Redis memory is kept alongside, and deliberately: it is the *working* memory
the prompt is built from, bounded to a few turns and expiring with the session
token. Postgres is the record. Reading the prompt window out of Postgres would
put a database round trip on the answer path to reproduce something Redis
already holds.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from iam_platform.domain.ai_resources.entities import (
    ChatWidget,
    Conversation,
    ConversationMessage,
    ConversationState,
    MessageRole,
)

#: A conversation is titled from the visitor's first question, truncated. The
#: console lists threads by title, and "Website visitor" repeated forty times
#: is a list nobody can navigate.
MAX_TITLE_CHARS = 80
FALLBACK_TITLE = "Website visitor"


def title_from(question: str) -> str:
    cleaned = " ".join(question.split())
    if not cleaned:
        return FALLBACK_TITLE
    if len(cleaned) <= MAX_TITLE_CHARS:
        return cleaned
    return cleaned[: MAX_TITLE_CHARS - 1].rstrip() + "…"


async def ensure_visitor_conversation(
    uow: object,
    *,
    widget: ChatWidget,
    session_id: UUID,
    now: datetime,
    title: str | None = None,
) -> Conversation:
    """One conversation per widget session, created on first use.

    Found by `visitor_session_id` from the signed token -- never by an id the
    caller supplies, so a visitor has no conversation id to tamper with and
    cannot address a stranger's thread.
    """
    existing: Conversation | None = await uow.conversations.find_by_visitor_session(  # type: ignore[attr-defined]
        tenant_id=widget.tenant_id, visitor_session_id=session_id
    )
    if existing is not None:
        return existing
    conversation = Conversation(
        id=uuid4(),
        tenant_id=widget.tenant_id,
        assistant_id=widget.assistant_id,
        membership_id=None,
        visitor_session_id=session_id,
        widget_id=widget.id,
        title=title or FALLBACK_TITLE,
        state=ConversationState.AI_ACTIVE,
        created_at=now,
        updated_at=now,
        last_message_at=now,
    )
    await uow.conversations.add(conversation)  # type: ignore[attr-defined]
    return conversation


async def append_exchange(
    uow: object,
    *,
    conversation: Conversation,
    question: str,
    answer: str,
    now: datetime,
) -> None:
    """Records one question-and-answer pair as two consecutive turns.

    Both in a single `add_many` so a crash between them cannot leave a
    question with no answer *and* a sequence number already consumed --
    `next_seq` is read once and the pair is written together.

    An empty answer is still written. A visitor whose answer failed asked the
    question all the same, and a thread that silently drops those reads, to an
    agent picking it up later, as though the assistant was never asked.
    """
    seq = await uow.conversation_messages.next_seq(conversation.id)  # type: ignore[attr-defined]
    await uow.conversation_messages.add_many(  # type: ignore[attr-defined]
        [
            ConversationMessage(
                id=uuid4(),
                tenant_id=conversation.tenant_id,
                conversation_id=conversation.id,
                seq=seq,
                role=MessageRole.USER,
                content=question,
                created_at=now,
            ),
            ConversationMessage(
                id=uuid4(),
                tenant_id=conversation.tenant_id,
                conversation_id=conversation.id,
                seq=seq + 1,
                role=MessageRole.ASSISTANT,
                content=answer,
                created_at=now,
            ),
        ]
    )
    conversation.record_turn(now=now)
    await uow.conversations.save(conversation)  # type: ignore[attr-defined]
