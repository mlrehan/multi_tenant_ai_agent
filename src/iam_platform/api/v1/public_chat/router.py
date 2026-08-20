"""`/v1/public/chat/*` -- the unauthenticated widget surface.

**Deliberately not under `/v1/tenants/{tenant_id}`.** Every route in that tree
runs through `get_current_claims` and `get_effective_tenant_permissions`, and a
public endpoint living there would be one forgotten dependency away from either
requiring a login it cannot have, or -- far worse -- appearing to be protected
while it is not. A separate prefix makes "this is the anonymous surface"
visible in the URL, in the router file, and in any audit of what is exposed.

There is also no tenant id in these paths. A caller who could name a tenant
could try naming someone else's; instead the tenant is *derived* from the
widget's public key and then from the signed session token, never accepted from
the request.

CORS is answered per widget from its own allowlist rather than by a global
middleware policy, because the whole point is that each tenant permits a
different set of sites.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse

from iam_platform.api.deps.authn import get_container
from iam_platform.api.deps.container import AppContainer
from iam_platform.api.v1.public_chat import schemas
from iam_platform.application.ai_resources.answer_question import AnswerQuestion
from iam_platform.application.ai_resources.exceptions import WidgetUnavailableError
from iam_platform.application.ai_resources.handoff import HandoffOffer
from iam_platform.application.ai_resources.notify_agents import NotifyAgentsOfHandoff
from iam_platform.application.ai_resources.public_chat import (
    AskWidget,
    AskWidgetCommand,
    StartWidgetSession,
    StartWidgetSessionCommand,
)
from iam_platform.application.ai_resources.public_conversation import (
    AdvanceHandoffFallback,
    AdvanceHandoffFallbackCommand,
    ReadVisitorMessages,
    ReadVisitorMessagesQuery,
    SendVisitorMessage,
    SendVisitorMessageCommand,
    SetVisitorTyping,
    SetVisitorTypingCommand,
)
from iam_platform.application.ai_resources.public_handoff import (
    OfferWidgetHandoff,
    SelectHandoffTeam,
    SelectHandoffTeamCommand,
    WidgetHandoffOfferQuery,
)
from iam_platform.core.errors import TokenError
from iam_platform.domain.ai_resources.chatbot import (
    DEFAULT_AVATAR_KEY,
    DEFAULT_CHATBOT_NAME,
    DEFAULT_CHATBOT_TITLE,
)
from iam_platform.domain.ai_resources.handoff_intent import wants_a_human

logger = logging.getLogger("iam_platform.api.v1.public_chat")

router = APIRouter(prefix="/v1/public/chat", tags=["public-chat"])


#: Read once at import, not per request. It is a file shipped inside the
#: package, so it cannot change while the process runs, and re-reading it on
#: every embed would put disk I/O on a path third-party pages hit.
_WIDGET_SCRIPT = (Path(__file__).parent / "widget.js").read_text(encoding="utf-8")


@router.get("/widget.js")
async def widget_script() -> Response:
    """Serves the embeddable script itself.

    `Access-Control-Allow-Origin: *` is correct *here* and nowhere else on this
    surface: this is a static asset with no tenant data in it, identical for
    every caller. A `<script src>` tag does not need CORS at all, but a page
    with a strict CSP may fetch it via `fetch()` instead, and refusing that
    would be a puzzle with no security benefit.

    Cached for an hour. Longer would leave a fix to the widget stuck in
    browsers; shorter would re-fetch it on pages that embed it site-wide.
    """
    return Response(
        content=_WIDGET_SCRIPT,
        media_type="application/javascript; charset=utf-8",
        headers={
            "Cache-Control": "public, max-age=3600",
            "Access-Control-Allow-Origin": "*",
            # Overrides the global `DENY`: this asset is *for* embedding, and
            # the header is meaningless on a script anyway -- but a scanner
            # reporting it as missing is a conversation nobody needs.
            "X-Frame-Options": "SAMEORIGIN",
        },
    )


@router.post("/session", response_model=schemas.WidgetSessionResponse)
async def start_widget_session(
    body: schemas.StartWidgetSessionRequest,
    response: Response,
    origin: str | None = Header(default=None),
    container: AppContainer = Depends(get_container),
) -> schemas.WidgetSessionResponse:
    """Exchanges a public key for a short-lived, tenant-scoped session token.

    The origin comes from the `Origin` **header**, never the body: a body field
    would let any caller simply assert an allowed origin, which is not a check
    at all.
    """
    resolved = await StartWidgetSession(
        container.public_widget_lookup, container.ai_resource_uow_factory
    ).execute(StartWidgetSessionCommand(public_key=body.public_key, origin=origin))

    # A returning visitor keeps their thread. The resumed id is only honoured
    # when the old token was minted for *this* widget and *this* origin: a
    # token from one embed must not carry a session into another, or a visitor
    # who used the public site's widget could resurface inside a staff portal's
    # conversation simply by holding a token from the first.
    resumed: UUID | None = None
    if body.resume_token:
        previous = container.widget_token_service.read_resumable(body.resume_token)
        if (
            previous is not None
            and previous.widget_id == resolved.widget_id
            and previous.origin == resolved.origin
        ):
            resumed = previous.session_id

    issued = container.widget_token_service.issue(
        widget_id=resolved.widget_id,
        tenant_id=resolved.tenant_id,
        knowledge_base_id=resolved.knowledge_base_id,
        origin=resolved.origin,
        now=container.clock.now(),
        session_id=resumed,
    )
    # Echoed back only because the request already passed the allowlist check.
    # Never `*`: with credentials disabled a wildcard would still let any site
    # read answers drawn from this tenant's knowledge base.
    _allow_origin(response, resolved.origin)

    return schemas.WidgetSessionResponse(
        session_token=issued.token,
        expires_at=issued.expires_at,
        # Defaults resolved server-side so one place decides what an
        # unconfigured widget looks like.
        chatbot_name=resolved.chatbot_name or DEFAULT_CHATBOT_NAME,
        chatbot_title=resolved.chatbot_title or DEFAULT_CHATBOT_TITLE,
        avatar_key=resolved.avatar_key or DEFAULT_AVATAR_KEY,
        greeting=resolved.greeting,
        show_quick_reply_suggestions=resolved.show_quick_reply_suggestions,
        quick_replies=list(resolved.quick_replies),
    )


@router.post("/ask")
async def ask_widget(
    body: schemas.AskWidgetRequest,
    request: Request,
    authorization: str | None = Header(default=None),
    container: AppContainer = Depends(get_container),
) -> StreamingResponse:
    """Answers a visitor's question, streamed, grounded in one knowledge base."""
    claims = _session_claims(container, authorization)

    use_case = AskWidget(
        container.public_widget_lookup,
        container.widget_quota,
        AnswerQuestion(
            container.ai_resource_uow_factory,
            container.vector_search_client,
            container.reranker,
            container.chat_model,
        ),
        container.widget_memory,
        # Persists the exchange after it streams, so an ordinary widget chat
        # appears in the tenant's console and falls under their retention
        # window -- previously only escalated chats were ever written.
        container.ai_resource_uow_factory,
        container.clock,
    )
    # **Both switches are read before the answer path**, so neither a visitor
    # asking for a colleague nor a tenant who has switched the AI off costs an
    # embedding, a rerank, a generation, or a unit of the daily allowance.
    #
    # `ai_chatbot_enabled` was previously stored, shown in the console, and
    # read by nothing on this path -- so turning the assistant "off" left the
    # widget answering, and billing, exactly as before. It is the master
    # switch, so it is enforced *here*, on the server, ahead of everything it
    # is supposed to prevent.
    policy = await OfferWidgetHandoff(
        container.public_widget_lookup, container.ai_resource_uow_factory
    ).policy(
        WidgetHandoffOfferQuery(widget_id=claims.widget_id, session_origin=claims.origin)
    )
    if not policy.ai_enabled or wants_a_human(body.question):
        if policy.handoff_allowed:
            # With the AI off this is every question, which is what the
            # console's own preview promises: "visitors are connected to a
            # person instead".
            return _handoff_offer_response(policy.offer, claims.origin, body.question)
        if not policy.ai_enabled:
            # AI off *and* transfers off. There is nothing this widget can do,
            # and saying so is the only honest answer -- falling through to the
            # model would ignore the switch the tenant just set.
            return _handoff_offer_response(
                HandoffOffer(
                    message=(
                        "Our assistant is unavailable at the moment. Please use "
                        "the contact details on our website and someone will be "
                        "able to help."
                    ),
                    teams=[],
                ),
                claims.origin,
                body.question,
            )
        # AI on, transfers off, and they asked for a person: unchanged -- the
        # model answers, and may say transfers are unavailable.

    result = await use_case.execute(
        AskWidgetCommand(
            widget_id=claims.widget_id,
            knowledge_base_id=claims.knowledge_base_id,
            question=body.question,
            # From the token, not the request: a stolen token must not work on
            # a different site simply by sending a different header.
            session_origin=claims.origin,
            session_id=claims.session_id,
        )
    )

    async def events() -> AsyncIterator[str]:
        # A visitor gets citation *locations*, never chunk or document ids.
        # Those are internal identifiers, and handing them to the open internet
        # would leak the shape of a tenant's corpus for no reader benefit.
        yield _sse(
            "sources",
            {
                "citations": [
                    {"label": c.label, "source": c.source_location}
                    for c in result.citations
                ]
            },
        )
        try:
            async for token in result.tokens:
                yield _sse("token", {"text": token})
        except Exception:
            logger.exception("public answer stream failed for widget %s", claims.widget_id)
            yield _sse("error", {"detail": "the answer could not be completed"})
            return
        yield _sse("done", {"cited": sorted(result.cited_labels)})

    streamed = StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-store"},
    )
    _allow_origin(streamed, claims.origin)
    del request
    return streamed


def _session_claims(container: AppContainer, authorization: str | None) -> Any:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise WidgetUnavailableError("a chat session is required")
    try:
        return container.widget_token_service.verify(authorization.split(" ", 1)[1])
    except TokenError as exc:
        # Deliberately the same error as "no such widget": an anonymous caller
        # learns only that the session is unusable, not whether it expired, was
        # forged, or names something real.
        raise WidgetUnavailableError("this chat session is not valid") from exc


def _allow_origin(response: Response, origin: str) -> None:
    """Per-widget CORS. Never `*` -- each tenant permits a different set of
    sites, and the origin echoed here has already passed the allowlist."""
    response.headers["Access-Control-Allow-Origin"] = origin
    # So a CDN does not serve one tenant's CORS header to another's visitor.
    response.headers["Vary"] = "Origin"


def _sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


def _handoff_offer_response(
    offer: Any, origin: str, question: str
) -> StreamingResponse:
    """The team menu, sent down the same SSE channel as an answer.

    Structured data (`event: handoff`), never HTML or a list the model wrote
    into its prose: the visitor's choice comes back as a team *id* the server
    validates, so a transfer cannot be steered by text.
    """

    async def events() -> AsyncIterator[str]:
        yield _sse(
            "handoff",
            {
                "message": offer.message,
                "reason": question[:500],
                "teams": [
                    {"id": t.id, "label": t.label, "description": t.description}
                    for t in offer.teams
                ],
            },
        )
        yield _sse("done", {"cited": []})

    streamed = StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-store"},
    )
    _allow_origin(streamed, origin)
    return streamed


@router.post("/handoff")
async def select_handoff_team(
    body: schemas.SelectTeamRequest,
    request: Request,
    authorization: str | None = Header(default=None),
    container: AppContainer = Depends(get_container),
) -> Response:
    """The visitor presses a team button and the conversation actually moves.

    Everything that decides the outcome comes from the *token* or the widget
    row -- the tenant, the session, the origin. The only thing the request
    supplies is which of that tenant's own teams was chosen, and that is
    validated against the tenant's rows before it is used.
    """
    claims = _session_claims(container, authorization)
    result = await SelectHandoffTeam(
        container.public_widget_lookup,
        container.ai_resource_uow_factory,
        container.clock,
        container.widget_memory,
        container.conversation_events,
        None,
        # Pushes to agents whose console is closed. Scoped to the team the
        # conversation was routed to -- see `NotifyAgentsOfHandoff`.
        NotifyAgentsOfHandoff(
            container.ai_resource_uow_factory, container.web_push, container.clock
        ),
    ).execute(
        SelectHandoffTeamCommand(
            widget_id=claims.widget_id,
            session_id=claims.session_id,
            session_origin=claims.origin,
            team_id=body.team_id,
            reason=body.reason,
        )
    )
    response = JSONResponse(
        {
            "message": result.message,
            "team": result.team_name,
            "last_seq": result.last_seq,
        },
        status_code=status.HTTP_200_OK,
    )
    _allow_origin(response, claims.origin)
    del request
    return response


@router.get("/messages")
async def read_visitor_messages(
    after: int = 0,
    history: int | None = None,
    before: int | None = None,
    authorization: str | None = Header(default=None),
    container: AppContainer = Depends(get_container),
) -> Response:
    """What a colleague has said to this visitor since `after`.

    The conversation is resolved from the *session* in the token; a visitor
    names no id, so there is none to tamper with. Internal comments -- the AI
    handoff summary among them -- are filtered by
    `MessageRole.visible_to_visitor` and can never appear here.
    """
    claims = _session_claims(container, authorization)
    # Advanced before the read, so a poll that trips the timeout returns the
    # notice it just wrote rather than making the visitor wait another four
    # seconds to see it. The visitor's poll is the tick: no scheduler, and the
    # elapsed time comes from stored rows, so a refresh cannot restart it.
    await AdvanceHandoffFallback(
        container.public_widget_lookup,
        container.ai_resource_uow_factory,
        container.clock,
        container.typing_indicators,
    ).execute(
        AdvanceHandoffFallbackCommand(
            widget_id=claims.widget_id,
            session_id=claims.session_id,
            session_origin=claims.origin,
        )
    )
    view = await ReadVisitorMessages(
        container.public_widget_lookup,
        container.ai_resource_uow_factory,
        container.typing_indicators,
    ).execute(
        ReadVisitorMessagesQuery(
            widget_id=claims.widget_id,
            session_id=claims.session_id,
            session_origin=claims.origin,
            after_seq=after,
            # `history` switches this read from "what has been said since" to
            # "the page before" -- the same rows, walked the other way, for
            # scrolling back through a long thread.
            history_limit=history,
            before_seq=before,
        )
    )
    response = JSONResponse(
        {
            "messages": [
                {"seq": t.seq, "author": t.author, "content": t.content,
                 "created_at": t.created_at}
                for t in view.turns
            ],
            # How the widget learns a colleague has handed the thread back.
            # Without it the transfer is one-way for the visitor's browser.
            "with_human": view.with_human,
            # Only true once a colleague has actually claimed it -- a queued
            # thread keeps the assistant answering while the visitor waits.
            "agent_engaged": view.agent_engaged,
            # Ephemeral, and read on the poll the widget already makes rather
            # than on a channel of its own: an indicator is only worth showing
            # while the visitor is looking at a conversation they are in.
            "agent_typing": view.agent_typing,
            "has_more": view.has_more,
        }
    )
    _allow_origin(response, claims.origin)
    return response


@router.post("/typing", status_code=status.HTTP_204_NO_CONTENT)
async def set_visitor_typing(
    body: schemas.VisitorTypingRequest,
    authorization: str | None = Header(default=None),
    container: AppContainer = Depends(get_container),
) -> Response:
    """Heartbeat: the visitor is composing something, or has stopped.

    Cheap on purpose -- this is the highest-frequency call on the anonymous
    surface. It writes one short-lived cache key and no rows, and the thread it
    belongs to is resolved from the signed token rather than named in the
    request.
    """
    claims = _session_claims(container, authorization)
    await SetVisitorTyping(
        container.public_widget_lookup,
        container.ai_resource_uow_factory,
        container.typing_indicators,
    ).execute(
        SetVisitorTypingCommand(
            widget_id=claims.widget_id,
            session_id=claims.session_id,
            session_origin=claims.origin,
            typing=body.typing,
        )
    )
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _allow_origin(response, claims.origin)
    return response


@router.post("/message")
async def send_visitor_message(
    body: schemas.VisitorMessageRequest,
    authorization: str | None = Header(default=None),
    container: AppContainer = Depends(get_container),
) -> Response:
    """The visitor replying to a colleague. Never reaches the AI or its quota."""
    claims = _session_claims(container, authorization)
    seq = await SendVisitorMessage(
        container.public_widget_lookup,
        container.ai_resource_uow_factory,
        container.clock,
        container.conversation_events,
    ).execute(
        SendVisitorMessageCommand(
            widget_id=claims.widget_id,
            session_id=claims.session_id,
            session_origin=claims.origin,
            content=body.content,
        )
    )
    response = JSONResponse({"seq": seq}, status_code=status.HTTP_201_CREATED)
    _allow_origin(response, claims.origin)
    return response
