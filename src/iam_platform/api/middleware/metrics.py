"""Prometheus-style request metrics -- docs/19-folder-structure.md's ``/metrics``.

Hand-rolled rather than pulling in ``prometheus-client``: the exposition
format is a few lines of text, and the alternative is a dependency whose
default registry is process-global mutable state -- which makes tests that
build more than one app instance interfere with each other. A per-app
collector keeps that contained.

**Label cardinality is the trap here.** Labelling by raw request path would
mint a new time series per tenant/resource UUID and eventually take down the
metrics backend, so the route *template* (``/v1/tenants/{tenant_id}/...``) is
used instead -- bounded by the number of routes, not the amount of data.
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

_LATENCY_BUCKETS_SECONDS = (0.005, 0.025, 0.1, 0.5, 1.0, 5.0)


class MetricsCollector:
    """Counters and latency histograms for HTTP traffic."""

    def __init__(self) -> None:
        self._requests: dict[tuple[str, str, int], int] = defaultdict(int)
        self._latency_sum: dict[tuple[str, str], float] = defaultdict(float)
        self._latency_buckets: dict[tuple[str, str, float], int] = defaultdict(int)

    def observe(self, *, method: str, route: str, status_code: int, duration: float) -> None:
        self._requests[(method, route, status_code)] += 1
        self._latency_sum[(method, route)] += duration
        for bucket in _LATENCY_BUCKETS_SECONDS:
            if duration <= bucket:
                self._latency_buckets[(method, route, bucket)] += 1

    def render(self) -> str:
        lines: list[str] = [
            "# HELP http_requests_total Total HTTP requests.",
            "# TYPE http_requests_total counter",
        ]
        for (method, route, status_code), count in sorted(self._requests.items()):
            lines.append(
                f'http_requests_total{{method="{method}",route="{route}",'
                f'status="{status_code}"}} {count}'
            )

        lines += [
            "# HELP http_request_duration_seconds Request latency.",
            "# TYPE http_request_duration_seconds histogram",
        ]
        for (method, route, bucket), count in sorted(self._latency_buckets.items()):
            lines.append(
                f'http_request_duration_seconds_bucket{{method="{method}",'
                f'route="{route}",le="{bucket}"}} {count}'
            )
        for (method, route), total in sorted(self._latency_sum.items()):
            lines.append(
                f'http_request_duration_seconds_sum{{method="{method}",route="{route}"}} {total}'
            )
        return "\n".join(lines) + "\n"


def _route_template(request: Request) -> str:
    """The matched route's path template, or ``unmatched``.

    Read from ``scope["route"]``, which Starlette's router populates during
    routing -- so this must be called *after* ``call_next``. Deliberately not
    re-implemented by iterating ``app.routes`` and calling ``Route.matches``:
    ``include_router`` keeps nested router objects in ``app.routes`` rather
    than flattening them, so a manual walk silently misses every mounted
    route and labels the whole API ``unmatched`` (observed while writing the
    cardinality test for this module).

    Falling back to a constant rather than the raw path is what bounds
    cardinality when someone probes nonexistent URLs -- otherwise a scanner
    hitting random paths would create an unbounded number of series.
    """
    path = getattr(request.scope.get("route"), "path", None)
    return path if isinstance(path, str) else "unmatched"


class MetricsMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: object, *, collector: MetricsCollector) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._collector = collector

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        started = time.perf_counter()
        response = await call_next(request)
        self._collector.observe(
            method=request.method,
            route=_route_template(request),
            status_code=response.status_code,
            duration=time.perf_counter() - started,
        )
        return response


def build_metrics_endpoint(
    collector: MetricsCollector,
) -> Callable[[], Awaitable[PlainTextResponse]]:
    async def metrics() -> PlainTextResponse:
        return PlainTextResponse(
            collector.render(), media_type="text/plain; version=0.0.4; charset=utf-8"
        )

    return metrics
