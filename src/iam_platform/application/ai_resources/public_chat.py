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

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from iam_platform.application.ai_resources.answer_question import (
    AnswerQuestion,
    AnswerStream,
)
from iam_platform.application.ai_resources.conversation_memory import ConversationMemory
from iam_platform.application.ai_resources.exceptions import (
    QuestionBlockedError,
    QuestionTooLongError,
    WidgetOriginNotAllowedError,
    WidgetQuotaExceededError,
    WidgetUnavailableError,
)
from iam_platform.application.ai_resources.ports import (
    AiResourceUowFactory,
    PublicWidgetLookup,
    WidgetMemoryStore,
    WidgetQuotaStore,
)
from iam_platform.application.ai_resources.visitor_conversation import (
    append_exchange,
    ensure_visitor_conversation,
    title_from,
)
from iam_platform.core.clock import Clock
from iam_platform.domain.ai_resources.chatbot import (
    DEFAULT_QUICK_REPLIES,
    HANDOFF_QUICK_REPLY,
)
from iam_platform.domain.ai_resources.entities import (
    ChatWidget,
    ConversationMessage,
    MessageRole,
)
from iam_platform.domain.ai_resources.guardrails import (
    MAX_QUESTION_CHARS,
    GuardrailCategory,
    screen_question,
)

#: A visitor's memory lives in Redis and is never persisted, so these fields
#: exist only to satisfy the shared `ConversationMessage` shape. Constants
#: rather than random ids: an all-zero uuid appearing in a log is obviously a
#: placeholder, where a plausible-looking one invites a search for the row.
logger = logging.getLogger("iam_platform.application.ai_resources.public_chat")

_ANONYMOUS = UUID(int=0)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


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

    #: How the tenant configured this widget to present itself. Carried on the
    #: result rather than left for the route to re-read the row: the session
    #: endpoint already loaded the widget, and a second read would be a second
    #: chance for the two to disagree.
    chatbot_name: str | None = None
    chatbot_title: str | None = None
    avatar_key: str | None = None
    greeting: str | None = None
    show_quick_reply_suggestions: bool = True

    #: The opening prompts, already resolved against the tenant's handoff
    #: policy. Sent as a finished list rather than a flag the widget interprets
    #: so the script holds no copy of the rule -- the console's preview and the
    #: embedded widget then cannot drift apart, which is the whole reason this
    #: field exists.
    quick_replies: tuple[str, ...] = ()


class StartWidgetSession:
    """Validates a public key + origin and returns what a session is scoped to.

    Token minting itself lives in the API layer, where the signing service is
    -- this use case decides *whether* a session may exist and what it may
    reach.
    """

    def __init__(
        self, lookup: PublicWidgetLookup, uow_factory: AiResourceUowFactory
    ) -> None:
        self._lookup = lookup
        self._uow_factory = uow_factory

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
            chatbot_name=widget.chatbot_name,
            chatbot_title=widget.chatbot_title,
            avatar_key=widget.avatar_key,
            greeting=widget.greeting,
            show_quick_reply_suggestions=widget.show_quick_reply_suggestions,
            quick_replies=await self._quick_replies(widget),
        )

    async def _quick_replies(self, widget: ChatWidget) -> tuple[str, ...]:
        """The pills this widget opens with, handoff pill included only if real.

        One extra read per session -- not per question -- and a session lasts
        thirty minutes, so this is a negligible cost for the difference between
        a button that transfers you and a button that quietly asks the model
        "Speak to a person".
        """
        if not widget.show_quick_reply_suggestions:
            return ()
        async with self._uow_factory(_ANONYMOUS, widget.tenant_id) as uow:
            settings = await uow.chatbot_settings.get_for_tenant(widget.tenant_id)
        # A tenant that has never opened the settings screen has no row, and
        # `allow_human_handoff` defaults to True -- so the absent row is
        # treated as the default, not as "off".
        allows_handoff = settings is None or settings.allow_human_handoff
        return DEFAULT_QUICK_REPLIES + ((HANDOFF_QUICK_REPLY,) if allows_handoff else ())


@dataclass(frozen=True, slots=True)
class AskWidgetCommand:
    widget_id: UUID
    knowledge_base_id: UUID
    question: str
    #: The origin the session was minted for, carried in the token and
    #: re-checked here -- a token stolen from one site must not work on
    #: another.
    session_origin: str
    #: Scopes this visitor's short-lived memory. From the token, never the
    #: request: a caller who could name someone else's session id would be
    #: reading a stranger's conversation.
    session_id: UUID


class AskWidget:
    def __init__(
        self,
        lookup: PublicWidgetLookup,
        quota: WidgetQuotaStore,
        answer_question: AnswerQuestion,
        memory: WidgetMemoryStore | None = None,
        uow_factory: AiResourceUowFactory | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._lookup = lookup
        self._quota = quota
        self._answer_question = answer_question
        # Optional so every existing construction site keeps working, and so a
        # deployment without Redis answers exactly as it did before -- which is
        # correct behaviour here rather than a degraded mode.
        self._memory = memory
        # Without these the widget answers but keeps no durable record, which
        # is what it did before conversations were persisted. Wired in
        # `bootstrap`; left optional so the many tests that drive this use case
        # with fakes need no database.
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: AskWidgetCommand) -> AnswerStream:
        widget = await self._reload(command)

        # **Screened here too, not only on the authenticated path.** This is
        # the surface anonymous strangers reach, so it is the one that most
        # needs the guardrail -- and it was the one missing it: `AskWidget`
        # calls `answer_from_namespace` directly and so never passed through
        # `AnswerQuestion.execute`, where the authenticated screening lives.
        # The structural defences still refused the question via the system
        # prompt, but it reached the model and was paid for.
        verdict = screen_question(command.question)
        if GuardrailCategory.EMPTY in verdict.categories:
            raise QuestionTooLongError("a question cannot be empty")
        if GuardrailCategory.TOO_LONG in verdict.categories:
            raise QuestionTooLongError(
                f"a question may be at most {MAX_QUESTION_CHARS} characters"
            )
        if not verdict.allowed:
            # Categories only -- the question may itself carry the secret
            # someone was fishing for, and this log is not the place for it.
            # A `security_events` row would be better, but that needs a unit of
            # work with tenant context and a visitor has none; recorded as a
            # known gap rather than faked.
            logger.warning(
                "widget question refused for widget %s: %s",
                widget.id,
                [c.value for c in verdict.categories],
            )
            raise QuestionBlockedError(
                "this question was refused: it asks for information this "
                "assistant will not provide"
            )

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
        # No store means the call is made exactly as it was before memory
        # existed -- not "with an empty memory", which would look the same
        # today and diverge the moment the parameter grows a default.
        if self._memory is None:
            stream = await self._answer_question.answer_from_namespace(
                verdict.text, namespace=_namespace_for(widget)
            )
        else:
            recent = await self._memory.recent(command.session_id)
            stream = await self._answer_question.answer_from_namespace(
                verdict.text,
                namespace=_namespace_for(widget),
                memory=_as_memory(recent),
            )

        if self._memory is None and self._uow_factory is None:
            # Neither sink wired: return the stream untouched rather than
            # wrapped in a recorder that records nowhere. The two look
            # identical today and diverge the moment the wrapper grows a
            # default.
            return stream

        stream.tokens = _remember(
            self._memory,
            command.session_id,
            verdict.text,
            stream.tokens,
            persist=self._persister(widget, command.session_id),
        )
        return stream

    def _persister(
        self, widget: ChatWidget, session_id: UUID
    ) -> Callable[[str, str], Awaitable[None]] | None:
        """Writes the exchange to `conversations` / `conversation_messages`.

        **Best effort, and deliberately so.** This runs after the visitor has
        already been given their answer; raising here would surface an error
        for work that succeeded, and would do it on the anonymous surface where
        the person can do nothing about it. A lost history row is the smaller
        harm than a failed answer, and it is logged.

        The consequence is stated rather than hidden: a persistence outage
        loses history for its duration, and retention has nothing to delete
        for those exchanges because nothing was written.
        """
        if self._uow_factory is None:
            return None

        async def persist(question: str, answer: str) -> None:
            try:
                now = self._clock.now() if self._clock else datetime.now(UTC)
                async with self._uow_factory(_ANONYMOUS, widget.tenant_id) as uow:  # type: ignore[misc]
                    conversation = await ensure_visitor_conversation(
                        uow,
                        widget=widget,
                        session_id=session_id,
                        now=now,
                        title=title_from(question),
                    )
                    await append_exchange(
                        uow,
                        conversation=conversation,
                        question=question,
                        answer=answer,
                        now=now,
                    )
            except Exception:
                logger.exception(
                    "could not persist the widget exchange for widget %s", widget.id
                )

        return persist

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


def _as_memory(recent: list[tuple[str, str]]) -> ConversationMemory:
    """Turns the session's stored `(role, content)` pairs into the same
    `ConversationMemory` the authenticated path builds.

    Deliberately reuses that type rather than formatting a prompt here: the
    rendering decides how history is fenced and labelled, and two versions of
    that would be two places for the "history is a record, not an instruction"
    guarantee to drift out of. A visitor's memory carries no summary -- a
    four-turn window never needs compacting.
    """
    return ConversationMemory(
        summary=None,
        recent=tuple(
            ConversationMessage(
                id=uuid4(),
                tenant_id=_ANONYMOUS,
                conversation_id=_ANONYMOUS,
                seq=index,
                role=MessageRole.USER if role == "user" else MessageRole.ASSISTANT,
                content=content,
                created_at=_EPOCH,
            )
            for index, (role, content) in enumerate(recent, start=1)
        ),
    )


async def _remember(
    memory: WidgetMemoryStore | None,
    session_id: UUID,
    question: str,
    tokens: AsyncIterator[str],
    persist: Callable[[str, str], Awaitable[None]] | None = None,
) -> AsyncIterator[str]:
    """Passes the answer through, then records the exchange.

    `finally`, like the authenticated path and for the same reason: a visitor
    who closes the tab mid-answer still asked the question, and the next one
    should not arrive as though the first never happened.

    Two sinks, on purpose. `memory` is the working window the next prompt is
    built from -- bounded, in Redis, expiring with the session. `persist` is
    the durable record the tenant's console reads and retention later deletes.
    Neither can stand in for the other: Redis forgets in thirty minutes, and
    reading the prompt window back out of Postgres would put a database round
    trip on the answer path to rebuild what Redis already has.
    """
    buffer = ""
    try:
        async for piece in tokens:
            buffer += piece
            yield piece
    finally:
        if memory is not None:
            await memory.append(session_id, question=question, answer=buffer)
        if persist is not None:
            await persist(question, buffer)
