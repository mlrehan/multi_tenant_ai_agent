"""A person's verdict on one AI answer: helpful or not, and optionally why.

**Append-only, and that is the design rather than a shortcut.** Feedback is a
record of what someone thought at the moment they read an answer. A reviewer
reading it later needs to see that record, not the latest edit of it -- so the
table revokes UPDATE and DELETE from the application role, the same treatment
`conversation_messages` and `audit_logs` get. The console locks the buttons
once a rating is sent, so a change of mind is not silently lost; it is simply
not offered.

**The question and answer are stored as the rater saw them.** A console answer
is never persisted anywhere else (the Ask panel is stateless), so without them
a "thumbs down" would record that *something* was bad with no way to find out
what. They are client-reported and bounded, and that is acceptable here: the
only data a forged value could corrupt is the tenant's own feedback about their
own assistant.

**Exactly one author.** A tenant member (the console) or an anonymous visitor
session (the widget), never both and never neither -- the same shape
`conversations` uses, enforced here and by a CHECK constraint.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

#: Bounds, mirrored by CHECK constraints so a direct write cannot exceed them
#: either. Generous for real use; small enough that feedback cannot be turned
#: into free storage by a stranger on a public page.
MAX_FEEDBACK_QUESTION_CHARS = 4000
MAX_FEEDBACK_ANSWER_CHARS = 20000
MAX_FEEDBACK_COMMENT_CHARS = 1000


class FeedbackRating(StrEnum):
    UP = "up"
    DOWN = "down"


class FeedbackInvalid(ValueError):
    """Raised by the entity; the application layer translates it."""


@dataclass(frozen=True, kw_only=True)
class AnswerFeedback:
    id: UUID
    tenant_id: UUID
    knowledge_base_id: UUID
    rating: FeedbackRating
    question: str
    answer: str
    comment: str | None
    created_at: datetime
    #: The console author. Set for a member, `None` for a visitor.
    membership_id: UUID | None = None
    #: The widget author. Both set for a visitor, both `None` for a member.
    widget_id: UUID | None = None
    visitor_session_id: UUID | None = None

    def __post_init__(self) -> None:
        member = self.membership_id is not None
        any_visitor_field = self.widget_id is not None or self.visitor_session_id is not None
        whole_visitor = self.widget_id is not None and self.visitor_session_id is not None
        by_member = member and not any_visitor_field
        by_visitor = whole_visitor and not member
        if not (by_member or by_visitor):
            raise FeedbackInvalid("feedback needs exactly one author")
        if not self.question.strip():
            raise FeedbackInvalid("feedback must say which question it is about")
        if not self.answer.strip():
            raise FeedbackInvalid("feedback must say which answer it is about")
        if len(self.question) > MAX_FEEDBACK_QUESTION_CHARS:
            raise FeedbackInvalid(
                f"the question may be at most {MAX_FEEDBACK_QUESTION_CHARS} characters"
            )
        if len(self.answer) > MAX_FEEDBACK_ANSWER_CHARS:
            raise FeedbackInvalid(
                f"the answer may be at most {MAX_FEEDBACK_ANSWER_CHARS} characters"
            )
        if self.comment is not None and len(self.comment) > MAX_FEEDBACK_COMMENT_CHARS:
            raise FeedbackInvalid(
                f"a comment may be at most {MAX_FEEDBACK_COMMENT_CHARS} characters"
            )
