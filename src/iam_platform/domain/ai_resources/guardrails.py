"""One place that decides whether text is safe to put in front of a model.

**Every string reaching the model passes through here, and the two entry points
are deliberately different functions.** A question is written *by* a person and
may be refused outright; a retrieved passage is content the tenant already chose
to index and must not be refused -- a document containing the words "ignore all
previous instructions" is usually a security policy, not an attack, and dropping
it would silently make the knowledge base wrong. So questions are *screened* and
passages are *neutralised*.

**What this layer is honestly for.** Blocklisting injection phrasing cannot be
won at the input: there are infinite paraphrases, and a filter that catches
today's wording gives the appearance of protection while the real defence
carries the weight. The real defences are structural and live elsewhere --
retrieved text is fenced, the system prompt says fenced content is never
instructions, every claim must cite a passage that was actually sent, and the
retrieval namespace is server-derived so no wording can reach another tenant's
data. This module exists for the things a *structural* defence cannot express:

* bounding size and stripping control characters, which are correctness issues
  before they are security ones;
* refusing the small set of requests that are unambiguous and have no legitimate
  phrasing -- "print your system prompt", "what is your API key";
* closing the fence-escape, which *is* a real structural hole: a passage
  containing this module's own delimiter could otherwise end the quoted region
  and have its remainder read as prompt.

Patterns are matched to *raise an event and refuse*, never to silently rewrite a
question into something the person did not ask.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum

#: A question, not a document. Longer input is a paste, and embedding it
#: produces a vector that means nothing in particular -- retrieval degrades
#: while still returning confident-looking results.
MAX_QUESTION_CHARS = 2000

#: Beyond this, a single retrieved passage is truncated rather than refused.
#: A pathologically long chunk crowds every other source out of the context
#: window, which is a quality failure that looks like a retrieval failure.
MAX_PASSAGE_CHARS = 8000

#: The delimiters `openai_chat.py` fences sources with. Duplicated here rather
#: than imported: `domain` may not import `infrastructure`, and this module's
#: whole job is to guarantee they cannot appear inside fenced content. A test
#: asserts the two stay in step.
FENCE_TOKENS = ("<<<SOURCE", "<<<END", ">>>")


class GuardrailCategory(StrEnum):
    """Why a question was refused. Recorded on the security event so an
    operator can tell an attack apart from someone pasting a novel."""

    EMPTY = "empty"
    TOO_LONG = "too_long"
    SYSTEM_PROMPT_EXTRACTION = "system_prompt_extraction"
    SECRET_EXTRACTION = "secret_extraction"
    CROSS_TENANT_PROBE = "cross_tenant_probe"


@dataclass(frozen=True, slots=True)
class GuardrailVerdict:
    """The cleaned text, or the reason there isn't any.

    Carries `categories` rather than a single reason: one question can trip
    several, and an operator reading the event log wants all of them.
    """

    text: str
    categories: tuple[GuardrailCategory, ...] = field(default=())

    @property
    def allowed(self) -> bool:
        return not self.categories


# Each pattern below is deliberately narrow. The test is not "could this be an
# attack?" -- almost anything could -- but "is there a legitimate question a
# tenant's user would ask that this also matches?" If yes, it does not belong
# here, because a false refusal on a real question is a worse product than a
# missed paraphrase whose damage the structural defences already bound.
_SYSTEM_PROMPT_EXTRACTION = re.compile(
    r"\b(?:"
    r"(?:reveal|show|print|repeat|output|display|disclose)\s+"
    r"(?:me\s+)?(?:your|the)\s+"
    r"(?:system\s+prompt|initial\s+instructions?|original\s+instructions?)"
    r"|what\s+(?:is|are)\s+your\s+(?:system\s+prompt|initial\s+instructions?)"
    r")\b",
    re.IGNORECASE,
)

_SECRET_EXTRACTION = re.compile(
    # `(?:\w+\s+)?` allows one qualifier between the article and the noun:
    # "the *OpenAI* API key" is how someone actually phrases this, and
    # requiring adjacency missed it. Bounded to a single word so the rule
    # cannot stretch across a sentence and start matching unrelated text.
    r"(?:\b(?:your|the)\s+(?:\w+\s+)?(?:api[\s_-]?key|secret[\s_-]?key|"
    r"access[\s_-]?token|credential|password|connection\s+string)\b)"
    r"|(?:\benv(?:ironment)?\s+variables?\b.*\b(?:print|show|list|dump)\b)"
    r"|(?:\b(?:print|show|list|dump)\b.*\benv(?:ironment)?\s+variables?\b)",
    re.IGNORECASE,
)

# No outer `\b(?:...)\b` wrapper: one alternative ends in `=` or `:`, and a
# trailing word boundary can never match after punctuation. That is exactly how
# `tenant_id = ...` walked through the first version of this rule -- found by
# the test, not by reading it.
_CROSS_TENANT_PROBE = re.compile(
    r"(?:\b(?:other|another|different|all)\s+"
    r"(?:tenants?|organi[sz]ations?|customers?|companies)\b)"
    r"|(?:\btenant[\s_-]?id\s*[=:])"
    r"|(?:\b(?:documents?|data|conversations?|files?)\s+(?:of|from|belonging\s+to)\s+"
    r"(?:other|another|a\s+different)\s+(?:tenant|organi[sz]ation|customer|company)\b)",
    re.IGNORECASE,
)

_RULES: tuple[tuple[GuardrailCategory, re.Pattern[str]], ...] = (
    (GuardrailCategory.SYSTEM_PROMPT_EXTRACTION, _SYSTEM_PROMPT_EXTRACTION),
    (GuardrailCategory.SECRET_EXTRACTION, _SECRET_EXTRACTION),
    (GuardrailCategory.CROSS_TENANT_PROBE, _CROSS_TENANT_PROBE),
)


def _strip_control_characters(text: str) -> str:
    """Removes characters that carry no meaning but change how text is read.

    Three separate problems, one pass:
    * Unicode category `Cc`/`Cf` covers ANSI escapes, NULs and the bidirectional
      overrides behind "Trojan Source" -- text that renders as one thing and is
      consumed as another.
    * A model reads them as tokens, so they are wasted budget at best.
    * Tab and newline are kept: they are real formatting in a pasted question.

    NFKC first, so a full-width or styled variant of a blocked word normalises
    to the form the patterns below actually match.
    """
    normalized = unicodedata.normalize("NFKC", text)
    return "".join(
        ch
        for ch in normalized
        if ch in "\t\n" or unicodedata.category(ch) not in ("Cc", "Cf")
    )


def screen_question(question: str) -> GuardrailVerdict:
    """Cleans and screens something a person typed.

    Returns rather than raises so the caller decides the consequence -- the
    authenticated console and the anonymous widget log the same refusal
    differently, and a use case that must record a security event needs the
    categories, not an exception.
    """
    cleaned = _strip_control_characters(question).strip()
    if not cleaned:
        return GuardrailVerdict(text="", categories=(GuardrailCategory.EMPTY,))
    if len(cleaned) > MAX_QUESTION_CHARS:
        return GuardrailVerdict(text=cleaned, categories=(GuardrailCategory.TOO_LONG,))

    matched = tuple(category for category, pattern in _RULES if pattern.search(cleaned))
    return GuardrailVerdict(text=cleaned, categories=matched)


def neutralize_passage(text: str) -> str:
    """Makes a retrieved passage safe to place inside a fenced block.

    **Never refuses.** This text is in the tenant's own knowledge base because
    they put it there; a passage that merely *discusses* prompt injection is
    ordinary content, and dropping it would leave the knowledge base quietly
    unable to answer questions about its own security policy.

    What it does close is the one hole a structural defence cannot: a passage
    containing the fence delimiter could otherwise terminate the quoted region
    early and have its remainder parsed as prompt. The delimiter is defanged, so
    the text still reads correctly to the model while no longer being able to
    escape the quotation. Control characters go for the same reasons as above --
    a bidirectional override inside a cited source is exactly how a poisoned
    document would try to misrepresent what it says.
    """
    cleaned = _strip_control_characters(text)
    for token in FENCE_TOKENS:
        # Zero-width space between the characters: visually and semantically
        # identical to a reader and a model, no longer the delimiter to a parser.
        cleaned = cleaned.replace(token, token[0] + "​" + token[1:])
    if len(cleaned) > MAX_PASSAGE_CHARS:
        cleaned = cleaned[:MAX_PASSAGE_CHARS] + "\n[passage truncated]"
    return cleaned
