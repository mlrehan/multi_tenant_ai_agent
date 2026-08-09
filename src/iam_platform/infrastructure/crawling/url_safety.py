"""Refuses URLs that would turn the crawler into a request-forgery primitive.

**Why this module is the security core of Phase 12.** Everywhere else in this
platform, a tenant's input decides what *their own* data is read or written.
Here it decides what the *worker process* connects to — and the worker sits
inside this deployment's network, holding database credentials, with a route to
the cloud provider's metadata service. "Crawl this website" is, unguarded, an
invitation for any tenant admin to make the platform fetch
``http://169.254.169.254/latest/meta-data/iam/security-credentials/`` and index
the result into a knowledge base they can then read back. That is a full
credential exfiltration path built out of a feature request.

So this is classic SSRF, and it is defended the way SSRF has to be defended:

1. **Scheme allowlist.** ``http``/``https`` only. ``file://`` reads the
   worker's disk; ``gopher://``/``dict://`` are protocol-smuggling classics.
2. **Resolve, then check the resolved addresses** — not the hostname. A
   hostname allowlist is defeated by ``evil.test`` resolving to ``127.0.0.1``,
   which no amount of string inspection catches.
3. **Check every address the name resolves to**, not the first. A name with
   both a public and a loopback record would otherwise pass on the public one
   and connect on whichever the OS picks.
4. **Re-check on every redirect and every discovered link**, not once at
   submission. A public URL that 302s to ``169.254.169.254`` defeats a
   submit-time-only check completely.

Point 4 is why this function is called from inside the crawl loop rather than
only in the use case that accepts the URL: validating at the boundary and
trusting thereafter is exactly the mistake that makes most SSRF filters
decorative.

**The residual risk is DNS rebinding** — a name that resolves to a public
address when checked here and a private one when the HTTP client connects a
moment later. Closing that needs the connection pinned to the address that was
validated, which means reaching into the transport of whatever client
``crawl4ai`` uses. It is recorded in docs/03-threat-model.md rather than
silently ignored; the checks here raise the cost of the attack substantially
without eliminating it.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import SplitResult, urlsplit

_ALLOWED_SCHEMES = frozenset({"http", "https"})

#: Ports that are never a website. Blocking these does not stop SSRF on its own
#: -- the address checks do that -- but it removes the most valuable targets if
#: an address check is ever bypassed, and costs nothing.
_BLOCKED_PORTS = frozenset(
    {
        22,  # ssh
        25,  # smtp
        445,  # smb
        3306,  # mysql
        5432,  # postgres
        6379,  # redis
        9200,  # elasticsearch
        11211,  # memcached
        27017,  # mongodb
    }
)


class UnsafeCrawlTargetError(ValueError):
    """Raised for a URL this platform refuses to fetch.

    A ``ValueError`` so the application layer can map it to a 400 -- the
    tenant supplied something invalid, and telling them so is correct. The
    message deliberately names the *category* of refusal without echoing
    resolved IP addresses back, which would make this endpoint a convenient
    internal-network scanner.
    """


@dataclass(frozen=True, slots=True)
class UrlSafetyPolicy:
    """Configuration for the checks below.

    ``allow_private_network_targets`` exists for the genuine case of a tenant
    crawling an internal wiki on the same private network. It is a deployment
    decision, never a per-tenant one: a tenant must not be able to opt itself
    into reaching this platform's internals.
    """

    allow_private_network_targets: bool = False

    #: Loopback and link-local stay blocked even when private networks are
    #: allowed. 169.254.169.254 is the cloud metadata endpoint on AWS, GCP,
    #: Azure and DigitalOcean alike, and 127.0.0.1 is this very process --
    #: neither is ever a legitimate crawl target, on any network.
    def blocks(self, address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
        if address.is_loopback:
            return "a loopback address"
        if address.is_link_local:
            return "a link-local address (cloud metadata services live here)"
        if address.is_multicast:
            return "a multicast address"
        if address.is_reserved or address.is_unspecified:
            return "a reserved address"
        # IPv4-mapped IPv6 (::ffff:127.0.0.1) would otherwise sail past the
        # checks above, which are evaluated against the IPv6 form.
        mapped = getattr(address, "ipv4_mapped", None)
        if mapped is not None:
            return self.blocks(mapped)
        if address.is_private and not self.allow_private_network_targets:
            return "a private network address"
        return None


def assert_safe_to_fetch(url: str, policy: UrlSafetyPolicy) -> None:
    """Raises ``UnsafeCrawlTargetError`` unless this URL is safe to fetch.

    Call on every URL before fetching it: the submitted one, every redirect
    target, and every link discovered during a crawl.
    """
    parts = urlsplit(url)

    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        raise UnsafeCrawlTargetError(
            f"{url}: only http and https URLs can be crawled, not {parts.scheme or 'a relative URL'!r}"
        )

    hostname = parts.hostname
    if not hostname:
        raise UnsafeCrawlTargetError(f"{url}: no hostname to resolve")

    port = _port_for(parts)
    if port in _BLOCKED_PORTS:
        raise UnsafeCrawlTargetError(f"{url}: port {port} is not a web port")

    for address in _resolve_all(hostname, url=url):
        reason = policy.blocks(address)
        if reason is not None:
            # Deliberately does not include the resolved address: echoing it
            # back turns "which of these hostnames is internal?" into a
            # question this API answers for free.
            raise UnsafeCrawlTargetError(f"{url}: refuses to fetch {reason}")


def _port_for(parts: SplitResult) -> int:
    try:
        explicit = parts.port
    except ValueError as exc:  # "http://host:notanumber"
        raise UnsafeCrawlTargetError(f"invalid port in URL: {exc}") from exc
    if explicit is not None:
        return explicit
    return 443 if parts.scheme.lower() == "https" else 80


def _resolve_all(
    hostname: str, *, url: str
) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Every address the name resolves to, not just the first.

    A hostname with both a public A record and a loopback AAAA record would
    otherwise pass a first-record check and connect over the other one.
    """
    # A literal IP needs no DNS, and passing one to getaddrinfo would work but
    # obscures that no lookup happened.
    try:
        return [ipaddress.ip_address(hostname)]
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeCrawlTargetError(f"{url}: hostname could not be resolved") from exc

    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for info in infos:
        sockaddr = info[4]
        addresses.append(ipaddress.ip_address(sockaddr[0]))
    if not addresses:
        raise UnsafeCrawlTargetError(f"{url}: hostname resolved to no addresses")
    return addresses
