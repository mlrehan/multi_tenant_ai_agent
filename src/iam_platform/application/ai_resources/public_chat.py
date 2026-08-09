"""The public question-answering surface -- Phase 13 Part B.

**This is the only place in the platform where an unauthenticated stranger
reaches tenant data**, so it is worth being explicit about what stands between
them and it.

Every other endpoint answers "who are you, and what may you do?" from a user
account, a membership and resolved permissions. A visitor on a tenant's help
page has none of those. What they have is a widget's public key, which is
printed in the page source and therefore known to everyone. So the key is an
*identifier*, not a credential, and the actual constraints are:

1. **The widget row decides the knowledge base.** The visitor never names one.
   A session token is minted for exactly one `knowledge_base_id`, read off the
   widget, so no question can reach a different one -- or a different tenant.
2. **The origin allowlist decides who may mint a session.** Real against a
   browser (page JavaScript cannot forge `Origin`), and honestly weak against
   a non-browser client, which is why it is not the only control.
3. **A daily cap decides how much this costs.** Each question is an embedding,
   a rerank and a generation. The tenant embedding the widget is not the one
   paying, so the platform bounds it.
4. **A disabled widget stops answering immediately.** Checked on every
   question, not only at session issuance -- a session token lives for
   thirty minutes, and "turn it off" must mean now.

The answer itself goes through `AnswerQuestion.answer_from_namespace`, the
*same* pipeline the authenticated endpoint uses. A parallel implementation
would be a second place for the groundedness rules to drift out of.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from iam_platform.application.ai_resources.answer_question import (
    AnswerQuestion,
    AnswerStream,
)
from iam_platform.application.ai_resources.exceptions import (
    WidgetOriginNotAllowedError,
    WidgetQuotaExceededError,
    WidgetUnavailableError,
)
from iam_platform.application.ai_resources.ports import (
    PublicWidgetLookup,
    WidgetQuotaStore,
)
from iam_platform.domain.ai_resources.entities import ChatWidget


@dataclass(frozen=True, slots=True)
class StartWidgetSessionCommand:
    public_key: str
    #: Taken from the `Origin` header, never from the request body -- a body
    #: field would let a caller simply state an allowed origin.
    origin: str | None


@dataclass(frozen=True, slots=True)
class ResolvedWidget:
    widget_id: UUID
    tenant_id: UUID
    knowledge_base_id: UUID
    origin: str


class StartWidgetSession:
    """Validates a public key + origin and returns what a session is scoped to.

    Token minting itself lives in the API layer, where the signing service is
    -- this use case decides *whether* a session may exist and what it may
    reach.
    """

    def __init__(self, lookup: PublicWidgetLookup) -> None:
        self._lookup = lookup

    async def execute(self, command: StartWidgetSessionCommand) -> ResolvedWidget:
        widget = await self._lookup.find_by_public_key(command.public_key)

        # One error for "no such widget" and "widget disabled", deliberately.
        # Distinguishing them tells an anonymous caller whether a key they
        # guessed is real, which is a probing oracle for free.
        if widget is None or not widget.is_active:
            raise WidgetUnavailableError("this chat widget is not available")

        if not widget.permits_origin(command.origin):
            raise WidgetOriginNotAllowedError(
                "this chat widget is not permitted on this site"
            )

        return ResolvedWidget(
            widget_id=widget.id,
            tenant_id=widget.tenant_id,
            knowledge_base_id=widget.knowledge_base_id,
            # The *validated* origin, not the raw header, so what is recorded
            # in the session is what passed the check.
            origin=str(command.origin),
        )


@dataclass(frozen=True, slots=True)
class AskWidgetCommand:
    widget_id: UUID
    knowledge_base_id: UUID
    question: str
    #: The origin the session was minted for, carried in the token and
    #: re-checked here -- a token stolen from one site must not work on
    #: another.
    session_origin: str


class AskWidget:
    def __init__(
        self,
        lookup: PublicWidgetLookup,
        quota: WidgetQuotaStore,
        answer_question: AnswerQuestion,
    ) -> None:
        self._lookup = lookup
        self._quota = quota
        self._answer_question = answer_question

    async def execute(self, command: AskWidgetCommand) -> AnswerStream:
        widget = await self._reload(command)

        # Quota before generation, not after: the point is to not spend the
        # money, and checking afterwards would only record that it was spent.
        within_limit = await self._quota.consume(
            widget_id=widget.id, limit=widget.daily_question_limit
        )
        if not within_limit:
            raise WidgetQuotaExceededError(
                "this chat widget has reached its question limit for today"
            )

        # The namespace is derived from the *widget's* knowledge base, freshly
        # loaded -- never from the token's claim alone. A token is a bearer
        # artefact; the row is the record of truth, and re-reading it is what
        # makes "disable the widget" and "repoint it" take effect immediately.
        return await self._answer_question.answer_from_namespace(
            command.question, namespace=_namespace_for(widget)
        )

    async def _reload(self, command: AskWidgetCommand) -> ChatWidget:
        """Re-checks the widget on every question, not just at session start.

        A session token lives for thirty minutes. Without this, disabling a
        widget or changing its origins would take effect only as sessions
        expired -- so "turn it off" would mean "turn it off in half an hour",
        which is not what an operator dealing with abuse means.
        """
        widget = await self._lookup.find_by_widget_id(command.widget_id)
        if widget is None or not widget.is_active:
            raise WidgetUnavailableError("this chat widget is not available")
        if not widget.permits_origin(command.session_origin):
            raise WidgetOriginNotAllowedError(
                "this chat widget is not permitted on this site"
            )
        # A token naming a knowledge base the widget no longer points at is
        # refused rather than honoured: the claim is stale, the row is current.
        if widget.knowledge_base_id != command.knowledge_base_id:
            raise WidgetUnavailableError("this chat session is no longer valid")
        return widget


def _namespace_for(widget: ChatWidget) -> str:
    """Rebuilt from the widget's own ids, the same `{tenant}/{kb}` shape
    `VectorNamespaceFactory` produces. Derived rather than passed in, so a
    request cannot name the slice of the vector store it reads."""
    return f"{widget.tenant_id}/{widget.knowledge_base_id}"
