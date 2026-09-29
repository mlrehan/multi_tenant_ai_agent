"""Which client IP the API sees -- the key for the per-IP rate limit.

The server trusted `X-Forwarded-For` from any peer (`"*"`), so any caller
could choose its own rate-limit bucket by sending the header. Only loopback
and private-network peers are trusted now: the reverse proxy and the admin
console's server-side proxy sit there; a public address never does.

These run uvicorn's own proxy-header middleware with the configured trust
list, so they assert what the server will actually do, not a model of it.
"""

from __future__ import annotations

from typing import Any

import pytest
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from iam_platform.asgi import server_config
from iam_platform.core.config import Settings

pytestmark = pytest.mark.unit

TRUSTED = Settings.model_fields["forwarded_allow_ips"].default


async def _client_seen(peer: str, forwarded_for: str | None) -> str:
    seen: dict[str, Any] = {}

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        seen["client"] = scope["client"][0]

    headers = [(b"x-forwarded-for", forwarded_for.encode())] if forwarded_for else []
    scope = {
        "type": "http",
        "client": (peer, 40000),
        "headers": headers,
        "scheme": "http",
    }
    await ProxyHeadersMiddleware(app, trusted_hosts=TRUSTED)(scope, None, None)  # type: ignore[arg-type]
    return str(seen["client"])


class TestTheTrustList:
    def test_it_is_never_everyone(self) -> None:
        assert "*" not in TRUSTED

    def test_the_server_is_given_the_setting(self) -> None:
        settings = Settings(forwarded_allow_ips="10.1.2.3")  # type: ignore[call-arg]
        config = server_config(object(), settings, host="127.0.0.1", port=8000)
        assert config.forwarded_allow_ips == "10.1.2.3"
        assert config.proxy_headers is True


class TestWhichIpTheApiSees:
    async def test_the_proxy_on_the_docker_bridge_is_believed(self) -> None:
        # Nginx (or the console) reaching the container through Docker's
        # bridge, having set the visitor's real address.
        assert await _client_seen("172.18.0.1", "203.0.113.7") == "203.0.113.7"

    async def test_a_public_caller_cannot_choose_its_own_ip(self) -> None:
        # Reaching the API directly from a public address with a forged
        # header: the header is ignored and the real peer is used.
        assert await _client_seen("198.51.100.20", "1.2.3.4") == "198.51.100.20"

    async def test_behind_an_appending_proxy_the_forged_entry_is_skipped(self) -> None:
        # "forged, real": the right-most untrusted entry is the one the proxy
        # itself wrote.
        assert await _client_seen("172.18.0.1", "1.2.3.4, 203.0.113.7") == "203.0.113.7"

    async def test_no_header_means_the_peer_itself(self) -> None:
        assert await _client_seen("172.18.0.1", None) == "172.18.0.1"
