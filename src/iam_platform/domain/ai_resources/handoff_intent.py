"""Deciding that a visitor has asked for a person.

**Narrow on purpose, and the test is not "could this be a handoff request?" but
"is there an ordinary question this also matches?"** -- the same rule
`guardrails.py` applies. A false positive is worse than a miss here: the visitor
asked a question the knowledge base could have answered, and instead got a menu
of teams. A miss just means they rephrase, or the AI offers a transfer itself
because it cannot answer.

So this matches explicit requests only. "Can I speak to someone?" yes;
"who should I speak to about nut allergies?" no -- that is a question *about*
who handles something, which the knowledge base may well answer.

**This is not the only route to a handoff**, and deliberately so. The other two
are the AI deciding mid-answer that it cannot help (prompted for by
`prompt_layers`), and an agent moving the conversation from the console. This
one exists because "I want to talk to a human" must never be answered with a
document quotation.
"""

from __future__ import annotations

import re

#: Explicit asks. Anchored on a *request verb* plus a *person noun*, so a
#: sentence merely containing "human" or "agent" does not trip it -- "is this a
#: human or a bot?" is a question, not a request to be transferred.
_EXPLICIT_REQUEST = re.compile(
    r"\b(?:"
    r"(?:speak|talk|chat|connect|put\s+me\s+through|transfer\s+me)\s+"
    r"(?:to|with)?\s*(?:a|an|the|some(?:one|body))?\s*"
    r"(?:human|person|people|agent|advisor|adviser|staff|"
    r"representative|rep|colleague|someone|somebody|manager|team)"
    r"|"
    r"(?:real|actual|live)\s+(?:human|person|agent)"
    r"|"
    r"human\s+(?:agent|support|help|assistance)"
    r"|"
    r"(?:hand(?:\s|-)?off|escalate)\s+(?:me|this|to)"
    r")\b",
    re.IGNORECASE,
)

#: A second, weaker signal: the visitor saying the bot is not helping *and*
#: asking for something else. Requires both halves, because "this isn't working"
#: alone is as likely to be about a form on the page as about the assistant.
_DISSATISFIED_AND_ASKING = re.compile(
    r"\b(?:you|this|bot|chatbot|ai)\b.{0,40}\b(?:not|isn'?t|cannot|can'?t|won'?t)\b"
    r".{0,60}\b(?:help|understand|answer|work)\b"
    r".{0,80}\b(?:human|person|agent|someone|staff)\b",
    re.IGNORECASE | re.DOTALL,
)


def wants_a_human(question: str) -> bool:
    """True when the visitor has explicitly asked to be transferred.

    Deliberately does **not** consider whether the knowledge base could answer:
    a visitor who asks for a person has asked for a person, and second-guessing
    that with a retrieval score would be the assistant deciding it knows better.
    """
    text = (question or "").strip()
    if not text:
        return False
    return bool(_EXPLICIT_REQUEST.search(text) or _DISSATISFIED_AND_ASKING.search(text))
