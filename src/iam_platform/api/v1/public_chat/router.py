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

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import StreamingResponse

from iam_platform.api.deps.authn import get_container
from iam_platform.api.deps.container import AppContainer
from iam_platform.api.v1.public_chat import schemas
from iam_platform.application.ai_resources.answer_question import AnswerQuestion
from iam_platform.application.ai_resources.exceptions import WidgetUnavailableError
from iam_platform.application.ai_resources.public_chat import (
    AskWidget,
    AskWidgetCommand,
    StartWidgetSession,
    StartWidgetSessionCommand,
)
from iam_platform.core.errors import TokenError

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
    resolved = await StartWidgetSession(container.public_widget_lookup).execute(
        StartWidgetSessionCommand(public_key=body.public_key, origin=origin)
    )

    issued = container.widget_token_service.issue(
        widget_id=resolved.widget_id,
        tenant_id=resolved.tenant_id,
        knowledge_base_id=resolved.knowledge_base_id,
        origin=resolved.origin,
        now=container.clock.now(),
    )
    # Echoed back only because the request already passed the allowlist check.
    # Never `*`: with credentials disabled a wildcard would still let any site
    # read answers drawn from this tenant's knowledge base.
    _allow_origin(response, resolved.origin)
    return schemas.WidgetSessionResponse(
        session_token=issued.token, expires_at=issued.expires_at
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
    )
    result = await use_case.execute(
        AskWidgetCommand(
            widget_id=claims.widget_id,
            knowledge_base_id=claims.knowledge_base_id,
            question=body.question,
            # From the token, not the request: a stolen token must not work on
            # a different site simply by sending a different header.
            session_origin=claims.origin,
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
