# --------------------------------------------------------------
# src/iam_platform/application/ai_resources/answer_gate.py
# --------------------------------------------------------------

"""What an answer is allowed to show, decided after the model has written it.

The prompt tells the model to answer only from the sources. A live run showed
that is not enough: asked "What is the capital city of Australia?", it
replied "Canberra" from general knowledge, with no citation, and the visitor
received it. Nothing in code enforced the rule, because retrieval always
returns the *nearest* pages -- relevant or not -- so the "no passages, no
generation" guard never fires for an off-topic question.

Two mechanisms, both structural rather than guessed from wording:

1. **Held until cited.** The answer is not released to the caller until it
   cites a passage that was actually offered. An answer that finishes with no
   citation is replaced by `WITHHELD_REPLY` -- unless it falls under one of
   the exemptions below, where an uncited reply is legitimate or where
   replacing it could do harm. Measured on the live stack, a reasoning model
   emits its whole reply within about half a second of the first token, so
   holding costs almost nothing of a wait that is mostly spent before it.

2. **`NO_ANSWER_MARKER`.** The model is told to open with this marker when
   the sources do not answer the question. It is removed before display and
   recorded as `not_in_sources`, so "the sources don't say -- here is how to
   contact us [2]" reaches the "couldn't answer" feed even though it cites
   something. Before this, citing the contact page made such an answer count
   as `grounded`, and the gap it revealed was never shown to the tenant.

3. **`RESTRICTED_MARKER`.** The model opens with this one instead when it
   declines because of the tenant's own avoid rules. Recorded as `restricted`
   and kept *out* of the "couldn't answer" feed: the sources may well cover the
   question, and adding a document cannot change a deliberate restriction.
   Before it existed, such a decline used `NO_ANSWER_MARKER` and was listed as
   "Not covered by your sources" -- untrue, and advice the tenant could not act
   on.
"""

from __future__ import annotations

import re

from iam_platform.application.ai_resources.unanswered_questions import (
    is_about_the_assistant,
    is_small_talk,
)
from iam_platform.domain.ai_resources.guardrails import classify_nursery_risk
from iam_platform.domain.ai_resources.handoff_intent import wants_a_human

NO_ANSWER_MARKER = "[NO_ANSWER]"
RESTRICTED_MARKER = "[RESTRICTED]"
_MARKERS = (NO_ANSWER_MARKER, RESTRICTED_MARKER)
_LONGEST_MARKER = max(len(m) for m in _MARKERS)

#: Shown instead of an answer that did not come from the tenant's sources.
#: Points to a person rather than just refusing: the visitor still has a
#: question, and "speak to a person" is a phrase the handoff path recognises.
WITHHELD_REPLY = (
    "I couldn't find an answer to that in the information I've been given, "
    "so I'd rather not guess. If you'd like, ask to speak to a person and a "
    "member of the team can help."
)

#: A reply that routes someone to a safeguarding lead or an emergency service.
#: The platform prompt's explicit exception to "cite everything" is the
#: instruction to call 999/112; the safeguarding routes sit beside it. Matched
#: on the *reply* as well as the question, because the question-side risk
#: patterns are deliberately narrow -- "I think a child at the nursery is being
#: hurt" matches none of them, and replacing the model's "speak to the
#: Designated Safeguarding Lead" with a generic apology is the worst outcome
#: this gate could produce. The cost, accepted: a reply that merely mentions
#: one of these is shown even if it also strays from the sources.
_SAFETY_ROUTING = re.compile(
    r"(?<!\d)(?:999|112|111)(?!\d)|\b(?:"
    r"safeguarding|DSL|emergency\s+services?|police|ambulance|NSPCC|Childline|"
    r"social\s+services|children'?s\s+services|LADO"
    r")\b",
    re.IGNORECASE,
)

#: A reply that only asks the visitor something makes no factual claim, so
#: there is nothing for it to cite.
_MAX_CLARIFYING_CHARS = 300


def strip_marker(text: str) -> str:
    """Removes every marker; the visitor never sees either."""
    for marker in _MARKERS:
        text = text.replace(marker, "")
    return text


def should_withhold(question: str, answer: str) -> bool:
    """True when an answer that cited nothing must not be shown.

    The exemptions fail toward *showing* the answer, and deliberately so:

    * **Small talk** -- "hello" needs no source.
    * **A question about the assistant itself** -- "who are you?" is
      answered from the tenant's configured role, not from the sources.
    * **A request for a person** -- the reply is routing, not a fact.
    * **A nursery risk signal** (safeguarding, immediate danger, medical,
      custody, complaints, ...) -- the prompt tells the model to route these to
      a human or an emergency service without waiting for a source. Replacing
      "please speak to the Designated Safeguarding Lead now" with a generic
      "I couldn't find that" would be the worst outcome this gate could have.
    * **A reply routing to safeguarding or emergency help** (999, NHS 111,
      the DSL, police, ...) -- see `_SAFETY_ROUTING`.
    * **A short clarifying question** back to the visitor.

    The caller has already established that the answer cited nothing and
    carried no marker (a declared "the sources don't say" or "I can't discuss
    that" is honest as written and is shown).
    """
    if is_small_talk(question) or is_about_the_assistant(question) or wants_a_human(question):
        return False
    if classify_nursery_risk(question).has_risk or classify_nursery_risk(answer).has_risk:
        return False
    if _SAFETY_ROUTING.search(answer):
        return False
    reply = answer.strip()
    if not reply:
        return False
    is_clarifying_question = reply.endswith("?") and len(reply) <= _MAX_CLARIFYING_CHARS
    return not is_clarifying_question


def has_marker(text: str) -> bool:
    return any(marker in text for marker in _MARKERS)


def outcome_of(*, raw: str, cited: bool, withheld: bool) -> str:
    """The `answer_status` stored on the assistant turn, from what the model
    wrote (`raw`, markers and all) and what the pipeline did with it.

    A tenant restriction outranks "not in the sources": if the model declined
    because of an avoid rule, that is why it did not answer, whatever the
    sources hold.
    """
    if withheld:
        return "withheld"
    if RESTRICTED_MARKER in raw:
        return "restricted"
    if NO_ANSWER_MARKER in raw:
        return "not_in_sources"
    return "grounded" if cited else "uncited"


class HeldText:
    """Text on its way to the caller, with the marker kept out of it.

    The marker can arrive split across stream chunks ("[NO", "_ANS", "WER]"),
    so a trailing "[" with no "]" after it is kept back until the next chunk
    shows whether it is the marker or a citation like "[1]".
    """

    def __init__(self) -> None:
        self._pending = ""

    def push(self, piece: str) -> None:
        self._pending += piece

    def take(self, *, final: bool = False) -> str:
        text = strip_marker(self._pending)
        keep = ""
        if not final:
            cut = text.rfind("[")
            if cut != -1 and "]" not in text[cut:] and len(text) - cut < _LONGEST_MARKER:
                text, keep = text[:cut], text[cut:]
        self._pending = keep
        return text


__all__ = [
    "NO_ANSWER_MARKER",
    "RESTRICTED_MARKER",
    "WITHHELD_REPLY",
    "HeldText",
    "has_marker",
    "outcome_of",
    "should_withhold",
    "strip_marker",
]
