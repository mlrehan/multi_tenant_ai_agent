"""`UrlValidator` adapter over the SSRF guard.

Exists so the application layer can refuse an unsafe URL at the API boundary
without importing from `infrastructure` (docs/20-dependency-rules.md). The
guard itself lives one module over and is the same function the crawler calls
inside its own loop -- one implementation, two call sites, so the boundary
check and the in-loop check can never disagree about what "safe" means.

**This adapter translates the exception, and that is its second job.**
`url_safety` raises its own `UnsafeCrawlTargetError`, a plain `ValueError` --
correct for a module with no knowledge of HTTP. Left untranslated it would
reach the API as an unhandled exception and surface as a 500, hiding the one
message that tells the tenant what to change. The application-layer error of
the same name is mapped to 400 in `api/exception_handlers.py`.
"""

from __future__ import annotations

from iam_platform.application.ai_resources.exceptions import (
    UnsafeCrawlTargetError as ApplicationUnsafeCrawlTargetError,
)
from iam_platform.infrastructure.crawling.url_safety import (
    UnsafeCrawlTargetError,
    UrlSafetyPolicy,
    assert_safe_to_fetch,
)


class SsrfUrlValidator:
    def __init__(self, policy: UrlSafetyPolicy) -> None:
        self._policy = policy

    def assert_safe(self, url: str) -> None:
        try:
            assert_safe_to_fetch(url, self._policy)
        except UnsafeCrawlTargetError as exc:
            # The message is carried through deliberately: it names the
            # *category* of refusal ("a link-local address") without echoing
            # resolved IPs, so it is safe to show and actually useful.
            raise ApplicationUnsafeCrawlTargetError(str(exc)) from exc
