"""Bounded conversation memory.

The property under test is not "history is remembered" -- that is easy and any
implementation that concatenates everything satisfies it. It is that the prompt
stays **bounded** as a thread grows, which is what makes long conversations
affordable and stops the oldest (most context-setting) turns from silently
falling out of the window.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from iam_platform.application.ai_resources.conversation_memory import (
    COMPACT_AFTER_MESSAGES,
    MAX_SUMMARY_CHARS,
    RECENT_TURNS,
    assemble,
    compaction_window,
    fold_summary,
    needs_compaction,
)
from iam_platform.domain.ai_resources.entities import (
    Conversation,
    ConversationMessage,
    ConversationStatus,
    MessageRole,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _conversation(*, summary: str | None = None, through: int = 0) -> Conversation:
    return Conversation(
        id=uuid4(),
        tenant_id=uuid4(),
        assistant_id=uuid4(),
        membership_id=uuid4(),
        status=ConversationStatus.ACTIVE,
        created_at=NOW,
        updated_at=NOW,
        summary=summary,
        summary_through_seq=through,
    )


def _messages(count: int, *, start: int = 1) -> list[ConversationMessage]:
    return [
        ConversationMessage(
            id=uuid4(),
            tenant_id=uuid4(),
            conversation_id=uuid4(),
            seq=start + i,
            role=MessageRole.USER if i % 2 == 0 else MessageRole.ASSISTANT,
            content=f"turn {start + i}",
            created_at=NOW,
        )
        for i in range(count)
    ]


class TestMemoryStaysBounded:
    def test_only_the_recent_window_is_kept_verbatim(self) -> None:
        """The whole point. A 100-turn thread must not put 100 turns in the
        prompt -- cost would grow quadratically and the earliest turns would
        fall out of the window unnoticed."""
        memory = assemble(_conversation(), _messages(100))
        assert len(memory.recent) == RECENT_TURNS
        # And it keeps the *latest* ones.
        assert memory.recent[-1].content == "turn 100"

    def test_a_short_thread_is_kept_whole(self) -> None:
        memory = assemble(_conversation(), _messages(3))
        assert [m.content for m in memory.recent] == ["turn 1", "turn 2", "turn 3"]

    def test_no_conversation_means_no_memory(self) -> None:
        """The public widget and a one-off question take this path; they must
        behave exactly as they did before memory existed."""
        assert assemble(None, _messages(10)).is_empty

    def test_the_rendered_block_carries_both_tiers(self) -> None:
        memory = assemble(_conversation(summary="Earlier: refunds discussed."), _messages(2))
        rendered = memory.render()
        assert "Earlier: refunds discussed." in rendered
        assert "turn 1" in rendered
        # Labelled by speaker, so the model can tell who said what.
        assert "User:" in rendered and "Assistant:" in rendered


class TestCompaction:
    def test_compaction_triggers_only_past_the_threshold(self) -> None:
        assert not needs_compaction(_messages(COMPACT_AFTER_MESSAGES))
        assert needs_compaction(_messages(COMPACT_AFTER_MESSAGES + 1))

    def test_the_verbatim_window_is_never_compacted(self) -> None:
        """Summarising turns that are *also* about to be sent verbatim would
        put the same content in the prompt twice."""
        messages = _messages(20)
        older, through = compaction_window(messages)
        assert len(older) == 20 - RECENT_TURNS
        assert through == older[-1].seq
        assert through == messages[-RECENT_TURNS - 1].seq

    def test_a_summary_cannot_move_backwards(self) -> None:
        """A stale job finishing after a newer one would otherwise re-expose
        turns the newer summary already covered."""
        conversation = _conversation(summary="new", through=10)
        conversation.compact(summary="stale", through_seq=5, now=NOW)
        assert conversation.summary == "new"
        assert conversation.summary_through_seq == 10

    def test_the_summary_itself_is_bounded(self) -> None:
        """Otherwise the one part of the prompt meant to *save* space grows
        without limit across a long thread."""
        folded = fold_summary("x" * MAX_SUMMARY_CHARS, "y" * 500)
        assert len(folded) <= MAX_SUMMARY_CHARS + 1  # +1 for the elision mark

    def test_truncation_keeps_the_newer_material(self) -> None:
        """A deliberate, stated loss: the oldest context is what someone is
        least likely to still be relying on by turn 40."""
        folded = fold_summary("old" * 2000, "the newest thing that happened")
        assert folded.endswith("the newest thing that happened")
        assert folded.startswith("…")
