"""`WebCrawler` backed by crawl4ai (Playwright/Chromium).

**Why a headless browser rather than `httpx` + BeautifulSoup.** The sites worth
ingesting -- documentation portals, help centres, knowledge bases -- are
overwhelmingly client-rendered. Fetching their HTML directly returns an empty
`<div id="root">` and a script tag, so a naive crawler indexes nothing and
reports success, which is worse than failing. crawl4ai drives a real browser
and returns cleaned markdown with navigation, headers, footers and scripts
stripped; indexing those would put a cookie banner into every chunk and make
retrieval *worse* the larger the site is.

**Every limit is enforced here, not merely passed along.** The link frontier is
bounded before it grows, the page budget is checked before each fetch, and the
job deadline is checked in the same place -- because "we asked crawl4ai for a
maximum" is a hope, and a bound this module owns is a guarantee. See
`core/config.py::CrawlSettings` for why each bound exists.

**The SSRF guard runs on every URL this module is about to request** --
including links discovered mid-crawl, which is the case a boundary-only check
misses entirely. See `url_safety.py` for the full reasoning.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import AsyncIterator, Sequence
from typing import Any
from urllib.parse import urldefrag, urljoin, urlsplit
from urllib.robotparser import RobotFileParser

from iam_platform.application.ai_resources.ports import (
    CrawledPage,
    CrawlLimits,
    CrawlMode,
)
from iam_platform.infrastructure.crawling.url_safety import (
    UnsafeCrawlTargetError,
    UrlSafetyPolicy,
    assert_safe_to_fetch,
)

logger = logging.getLogger("iam_platform.infrastructure.crawling")

_USER_AGENT = "iam-platform-knowledge-base-crawler"


class Crawl4AiWebCrawler:
    """Streams cleaned pages, bounded by depth, page count and wall-clock time."""

    def __init__(self, policy: UrlSafetyPolicy, *, crawler_factory: Any | None = None) -> None:
        self._policy = policy
        # Injectable so tests can drive the whole traversal -- frontier,
        # limits, robots, SSRF checks -- without launching Chromium. The
        # traversal logic is this module's own and is what needs testing;
        # crawl4ai fetching a page is not.
        self._crawler_factory = crawler_factory

    async def crawl(
        self, *, urls: Sequence[str], mode: CrawlMode, limits: CrawlLimits
    ) -> AsyncIterator[CrawledPage]:
        deadline = time.monotonic() + limits.job_timeout_seconds
        seen: set[str] = set()
        # (url, depth). A deque used as a FIFO gives breadth-first traversal:
        # depth-first would descend one branch of a site to the depth limit and
        # spend the entire page budget there, so a 500-page budget on a large
        # site would return one deep sliver instead of broad coverage.
        frontier: deque[tuple[str, int]] = deque()

        for raw in urls:
            normalized = _normalize(raw)
            if normalized not in seen:
                seen.add(normalized)
                frontier.append((normalized, 0))

        robots = _RobotsCache(enabled=limits.respect_robots_txt)
        indexed = 0

        async with _CrawlerSession(self._crawler_factory) as session:
            while frontier and indexed < limits.max_pages:
                if time.monotonic() > deadline:
                    logger.warning(
                        "crawl hit its %ss job deadline after %s pages",
                        limits.job_timeout_seconds,
                        indexed,
                    )
                    return

                url, depth = frontier.popleft()

                try:
                    assert_safe_to_fetch(url, self._policy)
                except UnsafeCrawlTargetError as exc:
                    # A refused *discovered* link is not a crawl failure -- a
                    # site legitimately links to localhost dashboards and
                    # intranet hosts. Skip it and keep going; only a refused
                    # *submitted* URL fails the job, and that is rejected at
                    # the API boundary before a job is ever enqueued.
                    logger.info("skipping unsafe link: %s", exc)
                    continue

                if not await robots.allows(url, session=session):
                    logger.info("robots.txt disallows %s", url)
                    continue

                page = await self._fetch(session, url, limits=limits)
                if page is None:
                    continue

                indexed += 1
                yield page

                if mode is CrawlMode.SITE and depth < limits.max_depth:
                    _extend_frontier(
                        frontier,
                        seen,
                        base_url=url,
                        links=page_links(page),
                        depth=depth + 1,
                        # Never queue more than could possibly be fetched: an
                        # unbounded frontier on a large site is a memory leak
                        # that outlives the page budget it can never spend.
                        capacity=limits.max_pages - indexed,
                    )

    async def _fetch(
        self, session: _CrawlerSession, url: str, *, limits: CrawlLimits
    ) -> CrawledPage | None:
        try:
            result = await asyncio.wait_for(
                session.arun(url), timeout=limits.page_timeout_seconds
            )
        except TimeoutError:
            logger.info("page timed out after %ss: %s", limits.page_timeout_seconds, url)
            return None
        except Exception:
            # One bad page must not end a 500-page crawl.
            logger.exception("failed to fetch %s", url)
            return None

        markdown = _markdown_of(result)
        if markdown is None:
            return None
        if len(markdown.encode("utf-8")) > limits.max_page_bytes:
            logger.info("skipping oversized page (%s): %s", limits.max_page_bytes, url)
            return None

        return CrawledPage(url=url, title=_title_of(result), markdown=markdown)


def page_links(page: CrawledPage) -> list[str]:
    """Markdown link targets, in document order.

    crawl4ai's own link extraction varies by version and returns absolute and
    relative forms inconsistently; parsing the markdown this module already
    holds is one behaviour rather than two.
    """
    import re

    return re.findall(r"\]\(\s*([^)\s]+)", page.markdown)


def _extend_frontier(
    frontier: deque[tuple[str, int]],
    seen: set[str],
    *,
    base_url: str,
    links: list[str],
    depth: int,
    capacity: int,
) -> None:
    if capacity <= 0:
        return
    base_host = urlsplit(base_url).hostname
    added = 0
    for link in links:
        if added >= capacity:
            return
        absolute = _normalize(urljoin(base_url, link))
        if absolute in seen:
            continue
        # Same-host confinement. "Crawl this website" means *this* website;
        # following outbound links would turn one job into an unbounded walk
        # of the public web, on the tenant's behalf and this platform's bill.
        if urlsplit(absolute).hostname != base_host:
            continue
        seen.add(absolute)
        frontier.append((absolute, depth))
        added += 1


def _normalize(url: str) -> str:
    """Drops the fragment, so `/page` and `/page#section` are one page rather
    than two identical documents indexed twice."""
    return urldefrag(url.strip()).url


def _markdown_of(result: Any) -> str | None:
    """crawl4ai has moved this field across versions (`markdown`,
    `markdown_v2`, `.raw_markdown`), so read defensively rather than pin to
    one shape and break silently on upgrade."""
    for attribute in ("markdown", "markdown_v2"):
        value = getattr(result, attribute, None)
        if value is None:
            continue
        text = getattr(value, "raw_markdown", value)
        if isinstance(text, str) and text.strip():
            return text
    return None


def _title_of(result: Any) -> str | None:
    metadata = getattr(result, "metadata", None)
    if isinstance(metadata, dict):
        title = metadata.get("title")
        if isinstance(title, str) and title.strip():
            return title.strip()
    return None


class _CrawlerSession:
    """Owns the browser for the life of one crawl.

    One browser for the whole job, not one per page: Chromium takes seconds to
    start, which on a 500-page crawl would dominate the runtime entirely.
    """

    def __init__(self, factory: Any | None) -> None:
        self._factory = factory
        self._crawler: Any = None

    async def __aenter__(self) -> _CrawlerSession:
        if self._factory is not None:
            self._crawler = self._factory()
        else:  # pragma: no cover - launches a real browser
            from crawl4ai import AsyncWebCrawler

            self._crawler = AsyncWebCrawler(verbose=False)
        enter = getattr(self._crawler, "__aenter__", None)
        if enter is not None:
            self._crawler = await enter()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        exit_ = getattr(self._crawler, "__aexit__", None)
        if exit_ is not None:
            await exit_(*exc_info)

    async def arun(self, url: str) -> Any:
        return await self._crawler.arun(url=url)

    async def fetch_text(self, url: str) -> str | None:
        """Plain text fetch, used only for `robots.txt`."""
        result = await self._crawler.arun(url=url)
        html = getattr(result, "html", None)
        return html if isinstance(html, str) else _markdown_of(result)


class _RobotsCache:
    """One `robots.txt` lookup per host, not per page.

    Re-fetching it for every URL on a 500-page crawl would triple the request
    count against a site this platform is trying to be polite to.
    """

    def __init__(self, *, enabled: bool) -> None:
        self._enabled = enabled
        self._parsers: dict[str, RobotFileParser | None] = {}

    async def allows(self, url: str, *, session: _CrawlerSession) -> bool:
        if not self._enabled:
            return True
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._parsers:
            self._parsers[origin] = await self._load(origin, session=session)
        parser = self._parsers[origin]
        if parser is None:
            # No robots.txt, or it could not be read. Absence of a policy is
            # permission -- treating an unreachable robots.txt as "deny all"
            # would make the feature fail on the many sites that simply do not
            # publish one.
            return True
        return parser.can_fetch(_USER_AGENT, url)

    async def _load(
        self, origin: str, *, session: _CrawlerSession
    ) -> RobotFileParser | None:
        try:
            body = await asyncio.wait_for(
                session.fetch_text(f"{origin}/robots.txt"), timeout=15
            )
        except Exception:
            logger.info("could not read robots.txt for %s", origin)
            return None
        if not body:
            return None
        parser = RobotFileParser()
        parser.parse(body.splitlines())
        return parser
