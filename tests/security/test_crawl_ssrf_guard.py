"""SSRF defence for URL/website ingestion (Phase 12).

This is the one place in the platform where tenant input decides what the
*worker process* connects to, rather than what tenant data is read. The worker
runs inside this deployment's network with database credentials and a route to
the cloud metadata service, so an unguarded crawler hands every tenant admin a
request-forgery primitive: point it at
``http://169.254.169.254/latest/meta-data/iam/security-credentials/``, let it
index the response into a knowledge base, then read the credentials back out
through the ordinary search API.

These tests are the proof that does not happen. Note what they deliberately do
*not* do: none of them stubs `assert_safe_to_fetch`. Each drives the real
function with the real resolver, because a test that mocks the check it is
verifying proves only that the mock was called.
"""

from __future__ import annotations

import pytest

from iam_platform.infrastructure.crawling.url_safety import (
    UnsafeCrawlTargetError,
    UrlSafetyPolicy,
    assert_safe_to_fetch,
)

pytestmark = pytest.mark.unit

DEFAULT = UrlSafetyPolicy()


class TestBlockedAddressRanges:
    @pytest.mark.parametrize(
        ("url", "what"),
        [
            ("http://169.254.169.254/latest/meta-data/", "AWS/GCP/Azure metadata"),
            ("http://169.254.170.2/v2/credentials", "ECS task metadata"),
            ("http://127.0.0.1:8000/v1/auth/login", "this very API"),
            ("http://localhost/readyz", "this very API by name"),
            ("http://[::1]/", "loopback over IPv6"),
            ("http://10.0.0.5/", "private class A"),
            ("http://172.16.4.1/", "private class B"),
            ("http://192.168.1.1/", "private class C — a home router"),
            ("http://0.0.0.0/", "the unspecified address"),
            ("http://[::ffff:127.0.0.1]/", "loopback smuggled as IPv4-mapped IPv6"),
        ],
    )
    def test_refuses(self, url: str, what: str) -> None:
        with pytest.raises(UnsafeCrawlTargetError):
            assert_safe_to_fetch(url, DEFAULT)


class TestBlockedSchemesAndPorts:
    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "file://C:/Windows/win.ini",
            "gopher://127.0.0.1:6379/_SET%20foo%20bar",
            "dict://127.0.0.1:11211/stats",
            "ftp://example.com/secrets.txt",
            "/relative/path",
        ],
    )
    def test_only_http_and_https_are_crawlable(self, url: str) -> None:
        with pytest.raises(UnsafeCrawlTargetError):
            assert_safe_to_fetch(url, DEFAULT)

    @pytest.mark.parametrize("port", [22, 5432, 6379, 27017])
    def test_refuses_non_web_ports_even_on_a_public_host(self, port: int) -> None:
        """Defence in depth. The address checks are what stop SSRF; this stops
        a *public* host being used to reach a service that happens to be
        exposed there, and costs nothing."""
        with pytest.raises(UnsafeCrawlTargetError, match="not a web port"):
            assert_safe_to_fetch(f"http://example.com:{port}/", DEFAULT)


class TestPublicTargetsAreAllowed:
    """The positive control. Without these, a guard that refused *everything*
    would pass every test above while making the feature useless."""

    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com/",
            "http://example.com/help/faq",
            "https://example.com:8443/docs",
            "https://93.184.216.34/",  # a public literal IP
        ],
    )
    def test_allows(self, url: str) -> None:
        assert_safe_to_fetch(url, DEFAULT)


class TestPrivateNetworkEscapeHatch:
    """A deployment crawling its own internal wiki is a real case, so the
    private ranges can be opened up. Loopback and link-local must stay shut
    even then -- they are never a legitimate crawl target on any network, and
    link-local is where the metadata service lives."""

    OPEN = UrlSafetyPolicy(allow_private_network_targets=True)

    def test_private_addresses_become_reachable(self) -> None:
        assert_safe_to_fetch("http://10.1.2.3/wiki", self.OPEN)

    @pytest.mark.parametrize(
        "url", ["http://127.0.0.1/", "http://169.254.169.254/", "http://[::1]/"]
    )
    def test_loopback_and_link_local_stay_blocked(self, url: str) -> None:
        with pytest.raises(UnsafeCrawlTargetError):
            assert_safe_to_fetch(url, self.OPEN)


class TestErrorsDoNotLeakTheInternalNetwork:
    def test_refusal_message_does_not_echo_the_resolved_address(self) -> None:
        """Otherwise this endpoint answers "does this hostname resolve
        internally?" for free, which is a network-mapping oracle."""
        with pytest.raises(UnsafeCrawlTargetError) as excinfo:
            assert_safe_to_fetch("http://localhost/admin", DEFAULT)

        message = str(excinfo.value)
        assert "127.0.0.1" not in message
        assert "::1" not in message
        assert "loopback" in message


class TestUnresolvableHostnames:
    def test_a_name_that_does_not_resolve_is_refused_not_crashed(self) -> None:
        """`socket.gaierror` escaping here would surface as an unhandled
        worker exception rather than a message the tenant can act on."""
        with pytest.raises(UnsafeCrawlTargetError, match="could not be resolved"):
            assert_safe_to_fetch(
                "http://no-such-host.invalid-tld-that-cannot-exist/", DEFAULT
            )
