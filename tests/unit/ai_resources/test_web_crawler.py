"""Crawl traversal: the frontier, the limits, and same-host confinement.

Driven with a fake browser rather than Chromium. That is not a shortcut — the
traversal is *this module's own logic* and is what can be wrong; crawl4ai
fetching a page is not. Launching a real browser here would make these tests
slow, network-dependent and flaky while testing someone else's code.

What is deliberately *not* faked: `assert_safe_to_fetch`. The SSRF guard runs
for real, because a test that stubs the check it is verifying proves only that
the stub was called.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from iam_platform.application.ai_resources.ports import CrawlLimits, CrawlMode
from iam_platform.infrastructure.crawling.crawl4ai_crawler import Crawl4AiWebCrawler
from iam_platform.infrastructure.crawling.url_safety import UrlSafetyPolicy

pytestmark = pytest.mark.unit


class _FakeResult:
    def __init__(self, markdown: str, title: str | None = None) -> None:
        self.markdown = markdown
        self.metadata = {"title": title} if title else {}
        self.html = markdown


class _FakeBrowser:
    """Serves a fixed site map. Records every URL requested, so a test can
    assert on what was *not* fetched — which is the whole point of a limit."""

    def __init__(self, pages: dict[str, str], *, robots: str | None = None) -> None:
        self._pages = pages
        self._robots = robots
        self.requested: list[str] = []

    async def arun(self, url: str) -> Any:
        self.requested.append(url)
        if url.endswith("/robots.txt"):
            return _FakeResult(self._robots or "")
        return _FakeResult(self._pages.get(url, ""), title=f"Title of {url}")


def _limits(**overrides: Any) -> CrawlLimits:
    base: dict[str, Any] = {
        "max_depth": 3,
        "max_pages": 500,
        "page_timeout_seconds": 30,
        "job_timeout_seconds": 7200,
        "respect_robots_txt": False,
        "max_page_bytes": 10 * 1024 * 1024,
    }
    base.update(overrides)
    return CrawlLimits(**base)


async def _collect(
    browser: _FakeBrowser, urls: Sequence[str], *, mode: CrawlMode, limits: CrawlLimits
) -> list[str]:
    crawler = Crawl4AiWebCrawler(UrlSafetyPolicy(), crawler_factory=lambda: browser)
    return [page.url async for page in crawler.crawl(urls=urls, mode=mode, limits=limits)]


class TestUrlListMode:
    async def test_fetches_exactly_the_given_urls_and_follows_nothing(self) -> None:
        browser = _FakeBrowser(
            {
                "https://example.com/a": "[onward](https://example.com/b)",
                "https://example.com/b": "unreached",
            }
        )

        crawled = await _collect(
            browser, ["https://example.com/a"], mode=CrawlMode.URL_LIST, limits=_limits()
        )

        assert crawled == ["https://example.com/a"]

    async def test_duplicate_urls_are_fetched_once(self) -> None:
        browser = _FakeBrowser({"https://example.com/a": "content"})

        crawled = await _collect(
            browser,
            ["https://example.com/a", "https://example.com/a#section"],
            mode=CrawlMode.URL_LIST,
            limits=_limits(),
        )

        # The fragment is stripped, so these are one page -- otherwise the same
        # content is indexed twice and pollutes every retrieval.
        assert crawled == ["https://example.com/a"]


class TestSiteMode:
    async def test_follows_links_breadth_first(self) -> None:
        browser = _FakeBrowser(
            {
                "https://example.com/": "[a](/a) [b](/b)",
                "https://example.com/a": "[deep](/a/deep)",
                "https://example.com/b": "leaf",
                "https://example.com/a/deep": "leaf",
            }
        )

        crawled = await _collect(
            browser, ["https://example.com/"], mode=CrawlMode.SITE, limits=_limits()
        )

        # Breadth-first: both depth-1 pages before the depth-2 one. Depth-first
        # would spend a whole page budget down one branch of a large site.
        assert crawled.index("https://example.com/b") < crawled.index(
            "https://example.com/a/deep"
        )

    async def test_stops_at_max_depth(self) -> None:
        browser = _FakeBrowser(
            {
                "https://example.com/": "[one](/one)",
                "https://example.com/one": "[two](/two)",
                "https://example.com/two": "[three](/three)",
                "https://example.com/three": "too deep",
            }
        )

        crawled = await _collect(
            browser,
            ["https://example.com/"],
            mode=CrawlMode.SITE,
            limits=_limits(max_depth=2),
        )

        assert "https://example.com/three" not in crawled
        assert "https://example.com/two" in crawled

    async def test_stops_at_max_pages(self) -> None:
        pages = {f"https://example.com/p{i}": f"[next](/p{i + 1})" for i in range(50)}
        browser = _FakeBrowser(pages)

        crawled = await _collect(
            browser,
            ["https://example.com/p0"],
            mode=CrawlMode.SITE,
            limits=_limits(max_pages=5, max_depth=99),
        )

        assert len(crawled) == 5

    async def test_does_not_leave_the_starting_host(self) -> None:
        """"Crawl this website" means *this* website. Following outbound links
        turns one job into an unbounded walk of the public web, on the tenant's
        behalf and this platform's bill."""
        browser = _FakeBrowser(
            {
                "https://example.com/": "[off](https://elsewhere.test/page) [on](/local)",
                "https://example.com/local": "leaf",
            }
        )

        crawled = await _collect(
            browser, ["https://example.com/"], mode=CrawlMode.SITE, limits=_limits()
        )

        assert crawled == ["https://example.com/", "https://example.com/local"]


class TestSafetyDuringTraversal:
    async def test_an_unsafe_discovered_link_is_skipped_and_the_crawl_continues(
        self,
    ) -> None:
        """The case a boundary-only SSRF check misses entirely.

        The *submitted* URL is a perfectly ordinary public page. A link **on
        that page** points somewhere this platform refuses to fetch. Nothing at
        the API boundary can catch that — the tenant never submitted it — so
        the guard has to run inside the crawl loop.

        The unsafe link is deliberately **on the same host** as the page it was
        found on. An off-host link would be dropped by same-host confinement
        anyway, so a test using one would pass with the SSRF guard entirely
        removed — proving nothing about the guard. A same-host link on a
        non-web port isolates it: only `assert_safe_to_fetch` can reject this.
        """
        browser = _FakeBrowser(
            {
                "https://example.com/": (
                    "[database](https://example.com:5432/) "
                    "[ssh](https://example.com:22/) "
                    "[legit](/help)"
                ),
                "https://example.com/help": "ordinary content",
            }
        )

        crawled = await _collect(
            browser, ["https://example.com/"], mode=CrawlMode.SITE, limits=_limits()
        )

        assert crawled == ["https://example.com/", "https://example.com/help"]
        # Refused *before* the fetch, not discarded after: nothing was ever
        # requested on those ports, so nothing could have been indexed.
        assert not any(":5432" in url for url in browser.requested)
        assert not any(":22/" in url for url in browser.requested)

    async def test_an_unsafe_submitted_url_is_never_fetched(self) -> None:
        browser = _FakeBrowser({"http://localhost:9999/": "unreachable anyway"})

        crawled = await _collect(
            browser, ["http://localhost:9999/"], mode=CrawlMode.SITE, limits=_limits()
        )

        assert crawled == []
        assert browser.requested == []


class TestRobotsTxt:
    async def test_disallowed_paths_are_not_fetched(self) -> None:
        browser = _FakeBrowser(
            {
                "https://example.com/public": "fine",
                "https://example.com/private": "should not be read",
            },
            robots="User-agent: *\nDisallow: /private",
        )

        crawled = await _collect(
            browser,
            ["https://example.com/public", "https://example.com/private"],
            mode=CrawlMode.URL_LIST,
            limits=_limits(respect_robots_txt=True),
        )

        assert crawled == ["https://example.com/public"]

    async def test_a_missing_robots_txt_permits_crawling(self) -> None:
        """Absence of a policy is permission. Treating an unreadable robots.txt
        as deny-all would break the feature on the many sites that publish
        none."""
        browser = _FakeBrowser({"https://example.com/page": "content"}, robots=None)

        crawled = await _collect(
            browser,
            ["https://example.com/page"],
            mode=CrawlMode.URL_LIST,
            limits=_limits(respect_robots_txt=True),
        )

        assert crawled == ["https://example.com/page"]

    async def test_robots_is_fetched_once_per_host(self) -> None:
        browser = _FakeBrowser(
            {f"https://example.com/p{i}": "content" for i in range(5)},
            robots="User-agent: *\nAllow: /",
        )

        await _collect(
            browser,
            [f"https://example.com/p{i}" for i in range(5)],
            mode=CrawlMode.URL_LIST,
            limits=_limits(respect_robots_txt=True),
        )

        assert browser.requested.count("https://example.com/robots.txt") == 1


class TestPageContent:
    async def test_empty_pages_are_skipped(self) -> None:
        browser = _FakeBrowser({"https://example.com/empty": "   "})

        crawled = await _collect(
            browser, ["https://example.com/empty"], mode=CrawlMode.URL_LIST, limits=_limits()
        )

        assert crawled == []

    async def test_oversized_pages_are_skipped(self) -> None:
        browser = _FakeBrowser({"https://example.com/huge": "x" * 5000})

        crawled = await _collect(
            browser,
            ["https://example.com/huge"],
            mode=CrawlMode.URL_LIST,
            limits=_limits(max_page_bytes=1000),
        )

        assert crawled == []

    async def test_a_failing_page_does_not_end_the_crawl(self) -> None:
        class _FlakyBrowser(_FakeBrowser):
            async def arun(self, url: str) -> Any:
                if url.endswith("/broken"):
                    raise RuntimeError("navigation failed")
                return await super().arun(url)

        browser = _FlakyBrowser(
            {
                "https://example.com/ok": "content",
                "https://example.com/also-ok": "content",
            }
        )

        crawled = await _collect(
            browser,
            [
                "https://example.com/broken",
                "https://example.com/ok",
                "https://example.com/also-ok",
            ],
            mode=CrawlMode.URL_LIST,
            limits=_limits(),
        )

        assert crawled == ["https://example.com/ok", "https://example.com/also-ok"]
