"""Per-client request rate limiting -- docs/19-folder-structure.md,
docs/03-threat-model.md (DoS row).

This is the *coarse* edge limit: a blanket per-IP cap that keeps a single
source from saturating the service. It is deliberately separate from the
fine-grained login-attempt throttling in ``application/identity`` (which is
per-account, progressive, and feeds account lockout) -- the two answer
different questions and must not be collapsed, since an attacker spreading
attempts across many accounts from one IP is caught here, and one spreading
across many IPs against one account is caught there.

**Fails closed on Redis errors** (docs/06-authorization-model.md: Redis "fails
closed (deny) if it can't confirm freshness, never fails open"). A rate
limiter that fails open is worth very little -- an attacker who can pressure
Redis gets unlimited requests exactly when the service is least able to
absorb them.

Health endpoints are exempt: rate-limiting an orchestrator's readiness probe
would make a pod look unhealthy under load and get it pulled from rotation,
which is precisely backwards.
"""

from __future__ import annotations

import logging

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from iam_platform.application.identity.ports import RateLimiter

logger = logging.getLogger("iam_platform.rate_limit")

_EXEMPT_PATHS = frozenset({"/livez", "/readyz", "/healthz", "/metrics"})


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app: object,
        *,
        rate_limiter: RateLimiter,
        limit: int = 300,
        window_seconds: int = 60,
    ) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._rate_limiter = rate_limiter
        self._limit = limit
        self._window_seconds = window_seconds

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path in _EXEMPT_PATHS:
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        key = f"ratelimit:ip:{client_ip}"

        try:
            allowed = await self._rate_limiter.check_and_increment(
                key, limit=self._limit, window_seconds=self._window_seconds
            )
        except Exception:
            logger.exception("rate limiter unavailable; failing closed")
            return JSONResponse(
                status_code=503, content={"detail": "service temporarily unavailable"}
            )

        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "rate limit exceeded"},
                headers={"retry-after": str(self._window_seconds)},
            )

        return await call_next(request)
