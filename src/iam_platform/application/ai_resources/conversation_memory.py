"""How much of a conversation goes back to the model, and in what form.

**Sending the whole thread every turn is the naive approach and it fails twice
over.** Cost grows quadratically with conversation length -- turn 50 re-sends
turns 1..49 -- and once the thread outgrows the context window the *oldest*
turns fall out silently, which is precisely backwards: the beginning of a
conversation is where the person stated what they were trying to do.

So memory is three tiers, assembled here:

1. **A rolling summary** of everything before `summary_through_seq`. Written
   once, re-read every turn, never recomputed from scratch.
2. **The recent turns verbatim**, because paraphrasing what someone said two
   messages ago is how an assistant starts answering a question that was not
   asked.
3. **Retrieved passages**, which are not memory at all and are assembled
   elsewhere -- they answer *this* question rather than recalling the thread.

The compaction threshold is a count of messages rather than tokens: token
counting needs the model's tokenizer, and being approximately right here costs
a few hundred tokens while being exactly right costs a dependency on the
request path. When the tail exceeds `COMPACT_AFTER_MESSAGES`, the older half is
summarised and folded into tier 1.
"""

from __future__ import annotations

from dataclasses import dataclass

from iam_platform.domain.ai_resources.entities import Conversation, ConversationMessage, MessageRole

#: Turns kept verbatim. Six is three exchanges -- enough for "and what about
#: the second one?" to resolve, short enough that the prompt stays small.
RECENT_TURNS = 6

#: Compact once the uncompacted tail is twice the verbatim window, so
#: summarisation runs every `RECENT_TURNS` turns rather than on every message.
COMPACT_AFTER_MESSAGES = RECENT_TURNS * 2

#: A summary longer than this is not a summary. Bounds the one part of the
#: prompt that would otherwise grow without limit across a long thread.
MAX_SUMMARY_CHARS = 1500


@dataclass(frozen=True, slots=True)
class ConversationMemory:
    """What the model is told about the conversation so far."""

    summary: str | None
    recent: tuple[ConversationMessage, ...]

    @property
    def is_empty(self) -> bool:
        return self.summary is None and not self.recent

    def render(self) -> str:
        """The memory block, as it appears in the prompt.

        Rendered as a transcript rather than as chat-completion `messages`
        because the platform's grounding rules live in one system prompt and
        one user message; splitting the thread across real message roles would
        give a poisoned earlier turn the same standing as the system prompt.
        Here it is quoted material, clearly labelled as history.
        """
        if self.is_empty:
            return ""
        parts: list[str] = []
        if self.summary:
            parts.append(f"Summary of earlier conversation:\n{self.summary}")
        if self.recent:
            turns = "\n".join(
                f"{'User' if m.role is MessageRole.USER else 'Assistant'}: {m.content}"
                for m in self.recent
            )
            parts.append(f"Recent turns:\n{turns}")
        return "\n\n".join(parts)


def assemble(
    conversation: Conversation | None, messages: list[ConversationMessage]
) -> ConversationMemory:
    """Builds the memory for the next turn.

    `messages` is expected to be the *uncompacted tail* -- the rows after
    `summary_through_seq` -- not the whole thread. Fetching only that range is
    what makes the read cheap; passing everything would still produce a correct
    prompt while defeating the entire point.
    """
    if conversation is None:
        return ConversationMemory(summary=None, recent=())
    tail = tuple(messages[-RECENT_TURNS:])
    return ConversationMemory(summary=conversation.summary, recent=tail)


def needs_compaction(messages: list[ConversationMessage]) -> bool:
    return len(messages) > COMPACT_AFTER_MESSAGES


def compaction_window(
    messages: list[ConversationMessage],
) -> tuple[list[ConversationMessage], int]:
    """The messages to summarise, and the `seq` the summary then reaches.

    Everything except the verbatim window: those turns are about to be sent in
    full anyway, and summarising them too would put the same content in the
    prompt twice.
    """
    older = messages[:-RECENT_TURNS]
    if not older:
        return [], 0
    return older, older[-1].seq


def fold_summary(previous: str | None, addition: str) -> str:
    """Joins a new precis onto the existing one, bounded.

    Truncates from the *front* when the cap is hit: the newer material is
    closer to what the conversation is currently about, and the oldest context
    is what a person is least likely to be relying on by turn 40. Stated
    plainly because it is a real, deliberate loss.
    """
    combined = f"{previous}\n{addition}".strip() if previous else addition.strip()
    if len(combined) <= MAX_SUMMARY_CHARS:
        return combined
    return "…" + combined[-MAX_SUMMARY_CHARS:]
