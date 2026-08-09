"""CORS preflight for the public widget surface.

**Why this exists at all.** The global `CORSMiddleware` is configured from
`settings.cors_allowed_origins` -- the admin console's origins, a fixed list
known at deploy time. The widget surface is the opposite: the set of permitted
sites is per widget, stored in the database, and changes whenever a tenant
edits their widget. Those two policies cannot share one middleware, and the
global one answers preflights for *every* path, so without this the browser
never gets past `OPTIONS` and the widget is unusable on any real page.

That was not a theoretical gap. Before this module existed, a preflight from a
legitimately allowlisted origin returned:

    HTTP/1.1 400 Bad Request
    access-control-allow-methods: GET
    Disallowed CORS origin, method

Every security test passed, and the live drive of Phase 13B passed, because
both used `curl` -- which never sends a preflight. Only a browser does.

**Why the preflight is answered permissively, and why that is not a hole.**
A preflight carries no body and no `Authorization` header, so at that moment
the server cannot know *which* widget is being addressed: the public key is in
the body of `/session`, and the session token is in the header of `/ask`.
There is nothing to check the origin against.

That costs nothing, because a preflight is not an access control. It only
tells the browser it may *send* the real request. The real request is where the
widget row is loaded and its allowlist enforced, and a browser will not expose
a response to the calling page unless that response itself carries a matching
`Access-Control-Allow-Origin`. Only the success paths in the public router set
that header, so a disallowed origin still gets nothing readable -- it just
learns so one round trip later.

The consequence worth knowing when debugging an embed: a rejected origin
surfaces in the host page as an opaque network error, not as a readable 403,
because withholding the header is precisely what makes the rejection stick.
The widget script says so in a console message rather than leaving the site
owner guessing.

`Access-Control-Allow-Credentials` is deliberately never sent. The widget
authenticates with a bearer token it holds in memory, not with cookies, so
credentialed CORS buys nothing and would forbid this echo-the-origin pattern
from ever being widened.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

#: Only this subtree. Scoped by prefix rather than applied globally so that a
#: future public route added elsewhere does not silently inherit an
#: echo-any-origin preflight policy it was never reviewed for.
PUBLIC_CHAT_PREFIX = "/v1/public/chat"

#: A day. Preflights are pure overhead on every question a visitor asks, and
#: the answer here does not depend on any state that can change -- the policy
#: that *can* change (the widget's allowlist) is enforced on the real request,
#: which is never cached.
_MAX_AGE_SECONDS = 86400


class PublicChatCorsMiddleware(BaseHTTPMiddleware):
    """Answers preflights for the public widget routes, before global CORS.

    Registered so that it runs *outside* `CORSMiddleware`: Starlette applies
    middleware in reverse registration order, so this must be added after it.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        origin = request.headers.get("origin")
        is_preflight = (
            request.method == "OPTIONS"
            and origin is not None
            and "access-control-request-method" in request.headers
        )
        on_public_path = request.url.path.startswith(PUBLIC_CHAT_PREFIX)
        if is_preflight and on_public_path:
            return Response(status_code=204, headers=_preflight_headers(origin or ""))

        response = await call_next(request)
        if on_public_path and origin and response.status_code >= 400:
            _allow_error_to_be_read(response, origin)
        return response


def _allow_error_to_be_read(response: Response, origin: str) -> None:
    """Lets the embedding page read an *error* from this surface.

    Success responses deliberately do **not** go through here. They echo the
    origin the widget row validated (via the router), not the origin that asked
    -- which is what stops a stolen session token being replayed from another
    site: the thief's browser refuses to hand them a response addressed to
    somebody else's origin.

    Errors are the opposite case. Their bodies are the deliberately opaque
    strings this surface uses to avoid being a probing oracle, so there is
    nothing in them to protect, and withholding the header was never a decision
    -- it was a side effect of which code path happened to set it.

    Driving the widget in a real browser is what exposed the cost. A disabled
    widget answers 404; without this header the browser discards it and hands
    the page a bare `TypeError`, so the widget's 401/404/429 branches were
    unreachable dead code. Every failure looked identical, including hitting
    the daily cap -- the one failure a visitor can actually act on ("try again
    tomorrow"). `curl` sees all of this fine, which is why every earlier check
    passed.
    """
    response.headers.setdefault("Access-Control-Allow-Origin", origin)
    existing = response.headers.get("Vary")
    if not existing:
        response.headers["Vary"] = "Origin"
    elif "origin" not in existing.lower():
        response.headers["Vary"] = f"{existing}, Origin"


def _preflight_headers(origin: str) -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
        # `Authorization` is the session token on `/ask`; without it here the
        # browser drops the header and every question arrives unauthenticated.
        "Access-Control-Allow-Headers": "Content-Type, Authorization",
        "Access-Control-Max-Age": str(_MAX_AGE_SECONDS),
        # The response varies by the request's origin, so a shared cache must
        # not serve one site's preflight answer to another's.
        "Vary": "Origin",
    }
