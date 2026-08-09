"""Binds a ``RequestContext`` (docs/20-dependency-rules.md contextvars rule) for
the lifetime of each request and echoes correlation/request IDs back to the
client -- used to join logs, audit rows, and client-side error reports."""

from __future__ import annotations

from uuid import UUID

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from iam_platform.core.context import bound, new_context


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get("x-correlation-id")
        correlation_id: UUID | None = None
        if incoming:
            try:
                correlation_id = UUID(incoming)
            except ValueError:
                correlation_id = None

        ctx = new_context(
            correlation_id=correlation_id,
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
        with bound(ctx):
            response = await call_next(request)

        response.headers["x-request-id"] = str(ctx.request_id)
        response.headers["x-correlation-id"] = str(ctx.correlation_id)
        return response
