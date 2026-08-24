"""HTTP-level tests for the embeddable widget surface.

These exist because of a defect that every other kind of test was structurally
blind to. The Phase 13B security tests drive the use cases directly, and the
live verification of that phase used `curl`. Neither sends a CORS preflight --
only a browser does -- so nobody noticed that the global `CORSMiddleware`
answered `OPTIONS /v1/public/chat/session` with

    400 Bad Request -- Disallowed CORS origin, method

for *every* origin, which meant the widget could not have worked on a single
real page. These tests are written at the HTTP boundary because that is the
only altitude at which a preflight exists at all.
"""

from __future__ import annotations

import re

import httpx
import pytest

pytestmark = pytest.mark.integration

WIDGET_ORIGIN = "https://help.acme.test"


class TestPreflight:
    async def test_a_preflight_to_the_public_surface_is_answered(
        self, client: httpx.AsyncClient
    ) -> None:
        resp = await client.request(
            "OPTIONS",
            "/v1/public/chat/session",
            headers={
                "Origin": WIDGET_ORIGIN,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )

        assert resp.status_code == 204
        assert resp.headers["access-control-allow-origin"] == WIDGET_ORIGIN
        assert "POST" in resp.headers["access-control-allow-methods"]

    async def test_the_preflight_permits_the_authorization_header(
        self, client: httpx.AsyncClient
    ) -> None:
        """`/ask` carries the session token in `Authorization`. Omitted from
        the allowed headers, the browser drops it and every question arrives
        unauthenticated -- a failure that looks like a bug in the token."""
        resp = await client.request(
            "OPTIONS",
            "/v1/public/chat/ask",
            headers={
                "Origin": WIDGET_ORIGIN,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )

        assert resp.status_code == 204
        allowed = resp.headers["access-control-allow-headers"].lower()
        assert "authorization" in allowed

    async def test_a_preflight_response_varies_by_origin(
        self, client: httpx.AsyncClient
    ) -> None:
        """Without this a shared cache can serve one site's preflight answer to
        another's visitors, and the echoed origin becomes meaningless."""
        resp = await client.request(
            "OPTIONS",
            "/v1/public/chat/session",
            headers={
                "Origin": WIDGET_ORIGIN,
                "Access-Control-Request-Method": "POST",
            },
        )

        assert "origin" in resp.headers["vary"].lower()

    @pytest.mark.parametrize(
        "path",
        [
            "/v1/tenants/00000000-0000-0000-0000-000000000000/knowledge-bases",
            "/v1/auth/login",
            "/v1/platform/users",
        ],
    )
    async def test_the_rest_of_the_api_is_not_widened(
        self, client: httpx.AsyncClient, path: str
    ) -> None:
        """The whole safety argument for echoing an arbitrary origin is that it
        is confined to the public subtree. If this ever passes, that argument
        has silently stopped being true and the admin API has become
        cross-origin callable from anywhere."""
        resp = await client.request(
            "OPTIONS",
            path,
            headers={
                "Origin": "https://evil.test",
                "Access-Control-Request-Method": "POST",
            },
        )

        assert resp.status_code == 400
        assert "access-control-allow-origin" not in resp.headers


class TestErrorsAreReadableByTheEmbeddingPage:
    async def test_an_error_from_the_public_surface_carries_cors_headers(
        self, client: httpx.AsyncClient
    ) -> None:
        """Found by driving the widget in a browser, invisible to `curl`.

        Without the header the browser discards the response and hands the page
        a bare `TypeError`, so the widget cannot tell a disabled widget from a
        quota exhaustion from an outage -- and the visitor who has hit the
        daily cap, the one case they can act on, is told nothing useful.
        """
        resp = await client.post(
            "/v1/public/chat/session",
            headers={"Origin": WIDGET_ORIGIN, "Content-Type": "application/json"},
            json={"public_key": "wk_definitely_not_a_real_key"},
        )

        assert resp.status_code == 404
        assert resp.headers["access-control-allow-origin"] == WIDGET_ORIGIN

    async def test_an_error_body_still_says_nothing_useful(
        self, client: httpx.AsyncClient
    ) -> None:
        """Making errors *readable* must not make them *informative*. The
        no-probing-oracle property from Phase 13B is in the body, not in the
        CORS header, and this proves the change did not quietly move it."""
        resp = await client.post(
            "/v1/public/chat/session",
            headers={"Origin": WIDGET_ORIGIN, "Content-Type": "application/json"},
            json={"public_key": "wk_definitely_not_a_real_key"},
        )

        detail = resp.json()["detail"].lower()
        assert "disabled" not in detail
        assert "not found" not in detail


class TestTheScriptIsActuallyServed:
    async def test_widget_js_is_served(self, client: httpx.AsyncClient) -> None:
        """Guards the packaging as much as the route.

        `widget.js` is a non-Python file inside the package. An editable
        install imports straight from `src/` and would serve it whether or not
        `[tool.setuptools.package-data]` names it, so this failing in CI after
        a real install is the signal that a built image would 500 on every
        embed -- the same shape as Phase 11's `.dockerignore` bug.
        """
        resp = await client.get("/v1/public/chat/widget.js")

        assert resp.status_code == 200
        assert "javascript" in resp.headers["content-type"]
        # Named markers rather than a size check: a truncated or wrong file
        # would still have a plausible length.
        assert "data-public-key" in resp.text
        assert "attachShadow" in resp.text

    async def test_the_script_is_cacheable_and_embeddable(
        self, client: httpx.AsyncClient
    ) -> None:
        """It is fetched by every visitor to every page that embeds it, so
        `no-store` (the app-wide default) would be a self-inflicted load
        problem."""
        resp = await client.get("/v1/public/chat/widget.js")

        assert "max-age" in resp.headers["cache-control"]
        assert resp.headers["access-control-allow-origin"] == "*"

    async def test_the_script_never_writes_untrusted_text_as_markup(
        self, client: httpx.AsyncClient
    ) -> None:
        """The answer is model output built from documents a tenant uploaded,
        rendered on a *customer's* page. Using `innerHTML` for it would turn a
        poisoned document into script execution on someone else's site.

        A source-level assertion is a blunt instrument, but the alternative is
        a browser test that only covers the payloads it thought to try.

        Matches *assignments*, not mentions. The first version of this test
        counted occurrences of the string and failed on the comment in
        `widget.js` explaining why `innerHTML` is not used -- the comment
        defending the property broke the test asserting it.
        """
        source = (await client.get("/v1/public/chat/widget.js")).text

        assignments = re.findall(r"\.innerHTML\s*=", source)
        # Exactly one: the static shell, written before any network call.
        assert assignments == [".innerHTML ="], assignments
        assert "root.innerHTML =" in source
        assert "textContent" in source
