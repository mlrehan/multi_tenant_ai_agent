"""Recording a rating of one AI answer, from the console or the public widget.

Two front doors, one entity, and **each door authorizes exactly as asking
through it does** -- deliberately reusing that path's own checks rather than a
second copy of them:

* The console reuses `ANSWER_QUESTION_PERMISSION`, `build_requester_context`
  and `load_visible_knowledge_base`. Anyone who could have asked the question
  may rate the answer; anyone who could not sees a 404, never a 403.
* The widget reuses `_require_widget` -- the same enabled-and-origin check the
  visitor's messages pass -- and takes the tenant and knowledge base from the
  *widget row*, never from the request or even the token alone.

Neither path ever reaches the model, either quota, or the conversation record.
Rating an answer is not asking a question.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from iam_platform.application.ai_resources.answer_question import (
    ANSWER_QUESTION_PERMISSION,
)
from iam_platform.application.ai_resources.authorize import load_visible_knowledge_base
from iam_platform.application.ai_resources.exceptions import (
    AnswerFeedbackInvalidError,
    AnswerFeedbackLimitError,
    KnowledgeBaseNotFoundError,
    PermissionDeniedError,
)
from iam_platform.application.ai_resources.ports import (
    AiResourceUowFactory,
    AnswerFeedbackRecord,
    PublicWidgetLookup,
)
from iam_platform.application.ai_resources.public_conversation import _require_widget
from iam_platform.application.ai_resources.requester import build_requester_context
from iam_platform.core.clock import Clock
from iam_platform.domain.ai_resources.feedback import (
    AnswerFeedback,
    FeedbackInvalid,
    FeedbackRating,
)

#: Ratings one anonymous widget session may leave. A real visitor rates a
#: handful of answers; this bounds what a script holding one session token can
#: accumulate, on top of the per-IP rate limit that bounds how fast.
MAX_FEEDBACK_PER_VISITOR_SESSION = 50


def _rating(value: str) -> FeedbackRating:
    try:
        return FeedbackRating(value)
    except ValueError:
        raise AnswerFeedbackInvalidError("a rating must be 'up' or 'down'") from None


def _comment(value: str | None) -> str | None:
    stripped = (value or "").strip()
    return stripped or None


@dataclass(frozen=True, slots=True)
class RecordAnswerFeedbackCommand:
    actor_user_id: str
    tenant_id: str
    knowledge_base_id: str
    permissions: frozenset[str]
    rating: str
    question: str
    answer: str
    comment: str | None = None


class RecordAnswerFeedback:
    """A tenant member rating an answer from the console's Ask panel."""

    def __init__(self, uow_factory: AiResourceUowFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: RecordAnswerFeedbackCommand) -> UUID:
        actor_id = UUID(command.actor_user_id)
        tenant_id = UUID(command.tenant_id)
        knowledge_base_id = UUID(command.knowledge_base_id)
        rating = _rating(command.rating)

        async with self._uow_factory(actor_id, tenant_id) as uow:
            if ANSWER_QUESTION_PERMISSION not in command.permissions:
                raise PermissionDeniedError(ANSWER_QUESTION_PERMISSION)

            requester = await build_requester_context(
                uow, tenant_id=tenant_id, user_id=actor_id, permissions=command.permissions
            )
            if requester is None:
                raise KnowledgeBaseNotFoundError(command.knowledge_base_id)

            # The same visibility check asking uses. A knowledge base the caller
            # could not have asked is one they cannot rate answers from either,
            # and failing it is a 404 -- it must not be provable to exist.
            knowledge_base = await load_visible_knowledge_base(
                uow,
                knowledge_base_id=knowledge_base_id,
                requester=requester,
                for_modification=False,
            )
            # Belt and braces. `load_visible_knowledge_base` leaves the
            # cross-*tenant* case to RLS, which hides the row entirely and is
            # proven live by the RLS suite. Feedback is a new write path, so it
            # also refuses here rather than resting on one mechanism -- the
            # composite FK in the migration is the third.
            if knowledge_base.tenant_id != tenant_id:
                raise KnowledgeBaseNotFoundError(command.knowledge_base_id)

            try:
                feedback = AnswerFeedback(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    knowledge_base_id=knowledge_base.id,
                    membership_id=requester.membership_id,
                    rating=rating,
                    question=command.question.strip(),
                    answer=command.answer.strip(),
                    comment=_comment(command.comment),
                    created_at=self._clock.now(),
                )
            except FeedbackInvalid as exc:
                raise AnswerFeedbackInvalidError(str(exc)) from exc

            await uow.answer_feedback.add(feedback)
            return feedback.id


@dataclass(frozen=True, slots=True)
class RecordWidgetFeedbackCommand:
    widget_id: UUID
    session_id: UUID
    session_origin: str
    rating: str
    question: str
    answer: str
    comment: str | None = None


class RecordWidgetFeedback:
    """An anonymous visitor rating an answer in the public widget."""

    def __init__(
        self, lookup: PublicWidgetLookup, uow_factory: AiResourceUowFactory, clock: Clock
    ) -> None:
        self._lookup = lookup
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: RecordWidgetFeedbackCommand) -> UUID:
        rating = _rating(command.rating)

        # Enabled, and embedded on a site it is allowed on -- the check every
        # other visitor write passes. A disabled widget stops taking feedback
        # the moment it stops taking questions.
        widget = await _require_widget(
            self._lookup, command.widget_id, command.session_origin
        )

        async with self._uow_factory(uuid4(), widget.tenant_id) as uow:
            used = await uow.answer_feedback.count_for_visitor_session(
                tenant_id=widget.tenant_id, visitor_session_id=command.session_id
            )
            if used >= MAX_FEEDBACK_PER_VISITOR_SESSION:
                raise AnswerFeedbackLimitError(
                    "thank you -- this chat has already sent as much feedback as it can"
                )

            try:
                feedback = AnswerFeedback(
                    id=uuid4(),
                    # Both from the widget row, never the request: a visitor
                    # cannot file feedback against another tenant, or another
                    # knowledge base of this one, by naming it.
                    tenant_id=widget.tenant_id,
                    knowledge_base_id=widget.knowledge_base_id,
                    widget_id=widget.id,
                    visitor_session_id=command.session_id,
                    rating=rating,
                    question=command.question.strip(),
                    answer=command.answer.strip(),
                    comment=_comment(command.comment),
                    created_at=self._clock.now(),
                )
            except FeedbackInvalid as exc:
                raise AnswerFeedbackInvalidError(str(exc)) from exc

            await uow.answer_feedback.add(feedback)
            return feedback.id


# ---------------------------------------------------------------- reviewing

#: Reading feedback means reading what visitors asked and what the assistant
#: said -- conversation content. So it takes the same permission as the
#: tenant's "All conversations" view and the Inbox, not a new one nothing
#: else checks.
REVIEW_FEEDBACK_PERMISSION = "tenant.conversations.view"

_RATINGS = {"up", "down"}
_CHANNELS = {"website", "console"}
MAX_FEEDBACK_PAGE = 100


def validate_review_filters(rating: str | None, channel: str | None, limit: int) -> None:
    if rating is not None and rating not in _RATINGS:
        raise AnswerFeedbackInvalidError("rating must be 'up' or 'down'")
    if channel is not None and channel not in _CHANNELS:
        raise AnswerFeedbackInvalidError("channel must be 'website' or 'console'")
    if not 1 <= limit <= MAX_FEEDBACK_PAGE:
        raise AnswerFeedbackInvalidError(f"limit must be between 1 and {MAX_FEEDBACK_PAGE}")


@dataclass(frozen=True, slots=True)
class ListAnswerFeedbackQuery:
    actor_user_id: str
    tenant_id: str
    permissions: frozenset[str]
    rating: str | None = None
    channel: str | None = None
    limit: int = 25
    offset: int = 0


@dataclass(frozen=True, slots=True)
class AnswerFeedbackPage:
    items: list[AnswerFeedbackRecord]
    #: How many ratings match the current filters -- what paging counts.
    total: int
    #: Tenant-wide counts, unfiltered: the headline numbers stay put while
    #: the list below them is narrowed.
    helpful: int
    not_helpful: int


class ListAnswerFeedback:
    """A tenant's own answer ratings, newest first, with headline counts."""

    def __init__(self, uow_factory: AiResourceUowFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, query: ListAnswerFeedbackQuery) -> AnswerFeedbackPage:
        tenant_id = UUID(query.tenant_id)
        validate_review_filters(query.rating, query.channel, query.limit)

        async with self._uow_factory(UUID(query.actor_user_id), tenant_id) as uow:
            if REVIEW_FEEDBACK_PERMISSION not in query.permissions:
                raise PermissionDeniedError(REVIEW_FEEDBACK_PERMISSION)
            # Always this tenant's own id, even though RLS would confine the
            # query anyway -- the tenant path never rests on one mechanism.
            items, total = await uow.answer_feedback.list_page(
                tenant_id=tenant_id,
                rating=query.rating,
                channel=query.channel,
                limit=query.limit,
                offset=max(0, query.offset),
            )
            summaries = await uow.answer_feedback.summarize(tenant_id=tenant_id)

        mine = next((s for s in summaries if s.tenant_id == tenant_id), None)
        return AnswerFeedbackPage(
            items=items,
            total=total,
            helpful=mine.helpful if mine else 0,
            not_helpful=mine.not_helpful if mine else 0,
        )
