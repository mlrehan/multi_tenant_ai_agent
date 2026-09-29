"""Questions the chatbot couldn't answer from the tenant's own sources.

The improvement loop for a non-technical administrator: a chatbot is only as
good as what it has been given, and the one thing they cannot see unaided is
*which* questions it failed. This lists them, most-asked first, so the next
page or document to add is obvious.

**What counts.** An assistant turn whose `answer_status` is `no_sources`
(retrieval found nothing), `not_in_sources` (the model declared the sources
don't answer it), `withheld` (an uncited answer was replaced -- see
`answer_gate`) or `uncited` (an exempt answer shown without a citation) --
recorded by the pipeline at answer time, never inferred from wording.
`restricted` (declined because of the tenant's own avoid rules) is
deliberately left out: adding a page cannot change a chosen restriction.

**Small talk is filtered out.** "hi", "thanks" and the like are answered
without sources *correctly*; listing them would bury the real gaps under
noise and teach the reader to ignore the list. The filter is deliberately a
short, literal list rather than a clever classifier: a false negative (a real
question hidden) is worse here than a false positive (one "hiya" shown).

**Shows visitors' own words**, so it is gated like reading conversations
(`tenant.conversations.view`) and scoped to the caller's tenant.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from iam_platform.application.ai_resources.exceptions import PermissionDeniedError
from iam_platform.application.ai_resources.ports import (
    AiResourceUowFactory,
    UnansweredQuestion,
)
from iam_platform.core.clock import Clock

VIEW_PERMISSION = "tenant.conversations.view"
WINDOW_DAYS = 30
MAX_ITEMS = 50

_SMALL_TALK = frozenset(
    {
        "hi", "hello", "hey", "hiya", "hi there", "hello there", "yo",
        "thanks", "thank you", "thank you so much", "thanks a lot", "cheers", "ta",
        "ok", "okay", "ok thanks", "okay thanks", "great", "perfect", "cool",
        "bye", "goodbye", "see you", "good morning", "good afternoon", "good evening",
        "yes", "no", "yep", "nope", "sure", "test", "testing",
    }
)


def is_small_talk(question: str) -> bool:
    """True for greetings, thanks and single-word acknowledgements."""
    bare = re.sub(r"[^\w\s]", "", question).strip().lower()
    bare = re.sub(r"\s+", " ", bare)
    return not bare or bare in _SMALL_TALK


#: Questions about the assistant itself. Since the tenant's chatbot brief
#: reaches the model, these are answered from that configuration -- correctly
#: without a source -- so they are neither withheld nor a gap in the sources.
_ABOUT_THE_ASSISTANT = re.compile(
    r"\bwho\s+are\s+you\b"
    r"|\bwhat\s+are\s+you\s*(?:\?|$|\band\b)"
    r"|\bwhat(?:'s|\s+is)\s+your\s+name\b"
    r"|\b(?:what|how)\s+can\s+you\s+(?:do|help)(?:\s+me)?(?:\s+with)?\s*(?:\?|$|\band\b)"
    r"|\bare\s+you\s+(?:a\s+)?(?:bot|robot|human|real\s+person|real|an?\s+ai|ai)\s*(?:\?|$)",
    re.IGNORECASE,
)


def is_about_the_assistant(question: str) -> bool:
    """True for "who are you?", "what can you help with?", "are you a bot?"."""
    return bool(_ABOUT_THE_ASSISTANT.search(question))


@dataclass(frozen=True, slots=True)
class ListUnansweredQuestionsQuery:
    actor_user_id: str
    tenant_id: str
    permissions: frozenset[str]
    days: int = WINDOW_DAYS
    limit: int = 20


class ListUnansweredQuestions:
    def __init__(self, uow_factory: AiResourceUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, query: ListUnansweredQuestionsQuery) -> list[UnansweredQuestion]:
        if VIEW_PERMISSION not in query.permissions:
            raise PermissionDeniedError(VIEW_PERMISSION)
        days = max(1, min(query.days, 365))
        limit = max(1, min(query.limit, MAX_ITEMS))
        tenant_id = UUID(query.tenant_id)
        since = self._clock.now() - timedelta(days=days)

        async with self._uow_factory(UUID(query.actor_user_id), tenant_id) as uow:
            # Over-fetched, because small talk is removed after the query and
            # the caller asked for `limit` real questions.
            rows = await uow.conversation_messages.unanswered_questions(
                tenant_id=tenant_id, since=since, limit=limit * 3
            )
        return [
            r
            for r in rows
            if not is_small_talk(r.question) and not is_about_the_assistant(r.question)
        ][:limit]
